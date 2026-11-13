"""Intake contracts, transactional publication, identity conflicts and checkpoint lineage. No Spark import needed."""

import json
from dataclasses import replace
from datetime import UTC, datetime

import psycopg
import pytest

from payrecon import db
from payrecon.streaming import intake, lineage
from payrecon.streaming.contract import ContractError, Dataset, decode, encode, utc
from payrecon.streaming.producer import DATASET_ID, fixture_records

DATASET = Dataset(DATASET_ID, utc("2026-10-01T00:00:00Z"), utc("2026-11-01T00:00:00Z"))
IDENTITY = intake.Identity("cluster-a", "topic-a", "case03.intake-check")


def deliveries():
    return [
        intake.Delivery(0, offset, key, value, datetime(2026, 11, 12, tzinfo=UTC))
        for offset, (key, value) in enumerate(fixture_records())
    ]


def test_duplicate_delivery_metadata_does_not_change_event_identity():
    records = fixture_records()
    event1, digest1 = decode(*records[1], DATASET)
    event2, digest2 = decode(*records[2], DATASET)
    assert event1 == event2 and digest1 == digest2
    late, _ = decode(*records[-1], DATASET)
    assert (utc(late["ingestion_time"]) - utc(late["event_time"])).total_seconds() == 86400


@pytest.mark.parametrize(
    "damage,reason",
    [
        ("boolean_amount", "invalid_schema"),
        ("decimal_amount", "invalid_schema"),
        ("sequence_overflow", "invalid_schema"),
        ("unknown_version", "unknown_version"),
        ("naive_time", "invalid_clock"),
        ("future_source", "invalid_clock"),
        ("hash", "hash_mismatch"),
        ("dataset", "wrong_dataset"),
        ("key", "key_mismatch"),
    ],
)
def test_unsafe_envelopes_are_rejected_without_coercion(damage, reason):
    key, value = fixture_records()[0]
    payload = json.loads(value)
    if damage == "boolean_amount":
        payload["event"]["amount_minor"] = True
    elif damage == "decimal_amount":
        payload["event"]["amount_minor"] = 42.0
    elif damage == "sequence_overflow":
        payload["event"]["sequence_no"] = 2**31
    elif damage == "unknown_version":
        payload["schema_version"] = True
    elif damage == "naive_time":
        payload["event"]["event_time"] = "2026-10-03T11:00:00"
    elif damage == "future_source":
        payload["event"]["ingestion_time"] = "2099-01-01T00:00:00Z"
    elif damage == "hash":
        payload["semantic_hash"] = "0" * 64
    elif damage == "dataset":
        payload["dataset_id"] = "another-dataset"
    else:
        key = b"another-payment"
    with pytest.raises(ContractError) as error:
        decode(key, json.dumps(payload).encode(), DATASET)
    assert error.value.reason == reason


@pytest.mark.parametrize("value", [b"{", b"\xff", b'{"schema_version":1,"schema_version":2}'])
def test_malformed_or_ambiguous_json_is_rejected(value):
    with pytest.raises(ContractError) as error:
        decode(b"pay", value, DATASET)
    assert error.value.reason == "malformed_json"


def test_prepare_refuses_the_batch_database_before_connecting(monkeypatch):
    monkeypatch.setenv("PGDATABASE", "payments")
    with pytest.raises(ValueError, match="separate payments_stream"):
        intake.prepare()


@pytest.fixture
def registered(con, tmp_path):
    db.migrate(con, intake.MIGRATIONS)
    checkpoint = tmp_path / "intake"
    generation = lineage.register(con, DATASET, IDENTITY, checkpoint)
    # Simulated marker for unit checks; the separate runtime integration uses real Spark metadata.
    checkpoint.mkdir()
    (checkpoint / "metadata").write_text("{}")
    return con, generation, checkpoint


@pytest.mark.db
def test_intake_archives_poison_keeps_day_late_event_and_replays_exactly(registered):
    con, generation, _ = registered
    original = deliveries()
    summary, reused = intake.persist_batch(con, DATASET, IDENTITY, generation, 0, original)
    assert not reused
    assert summary == dict(
        input=8,
        new_deliveries=8,
        new_events=3,
        duplicate_events=1,
        replayed_deliveries=0,
        quarantined=4,
        blocking=0,
    )
    assert con.execute("select count(*) from stream.delivery").fetchone()[0] == 8
    assert (
        con.execute(
            "select event->>'ingestion_time' from stream.intake_event where event_id='evt-900000202'"
        ).fetchone()[0]
        == "2026-10-04T11:00:05Z"
    )
    assert set(con.execute("select reason from stream.quarantine").fetchall()) == {
        ("malformed_json",),
        ("invalid_schema",),
        ("tombstone",),
        ("unknown_version",),
    }
    assert con.execute('select value from stream.delivery where "offset"=3').fetchone()[0] == b"{"
    before = con.execute("select committed_at from ops.stream_batch").fetchone()
    replay, reused = intake.persist_batch(con, DATASET, IDENTITY, generation, 0, original)
    assert reused and replay == summary
    assert con.execute("select committed_at from ops.stream_batch").fetchone() == before
    assert con.execute("select count(*) from stream.quarantine").fetchone()[0] == 4
    # Replayed transport under a new callback ID is also safe.
    newer, _ = intake.persist_batch(con, DATASET, IDENTITY, generation, 1, original)
    assert newer["new_deliveries"] == 0 and newer["replayed_deliveries"] == 8


@pytest.mark.db
@pytest.mark.parametrize("conflict", ["event_id", "sequence"])
def test_identity_conflict_is_archived_and_blocks_without_rewriting_canonical_event(registered, conflict):
    con, generation, _ = registered
    row = deliveries()[0]
    intake.persist_batch(con, DATASET, IDENTITY, generation, 0, [row])
    event = json.loads(row.value)["event"]
    event["amount_minor"] += 100
    if conflict == "sequence":
        event["event_id"] = "evt-other"
    changed = replace(row, offset=100, value=encode(event, DATASET.dataset_id))
    result, _ = intake.persist_batch(con, DATASET, IDENTITY, generation, 1, [changed])
    assert result["blocking"] == 1
    assert con.execute("select event->>'amount_minor' from stream.intake_event").fetchone()[0] == "4200"
    assert con.execute("select count(*) from stream.delivery").fetchone()[0] == 2
    with pytest.raises(intake.IntakeConflict, match="archived"):
        intake.halt_on_conflict(result)
    replayed, reused = intake.persist_batch(con, DATASET, IDENTITY, generation, 1, [changed])
    assert reused and replayed["blocking"] == 1


@pytest.mark.db
def test_changed_batch_or_transport_identity_never_silently_overwrites(registered):
    con, generation, _ = registered
    row = deliveries()[0]
    intake.persist_batch(con, DATASET, IDENTITY, generation, 0, [row])
    changed = replace(row, value=b"different bytes")
    with pytest.raises(intake.IntakeConflict, match="batch ID"):
        intake.persist_batch(con, DATASET, IDENTITY, generation, 0, [changed])
    with pytest.raises(intake.IntakeConflict, match="coordinate"):
        intake.persist_batch(con, DATASET, IDENTITY, generation, 1, [changed])
    assert con.execute("select count(*) from ops.stream_batch").fetchone()[0] == 1


@pytest.mark.db
def test_failure_after_raw_copy_rolls_back_raw_canonical_and_batch_log(registered):
    con, generation, _ = registered
    con.execute(
        "create function stream.inject_failure() returns trigger language plpgsql as $$ "
        "begin raise exception 'injected canonical failure'; end; $$"
    )
    con.execute(
        "create trigger injected before insert on stream.intake_event for each row "
        "execute function stream.inject_failure()"
    )
    with pytest.raises(psycopg.errors.RaiseException, match="injected canonical"):
        intake.persist_batch(con, DATASET, IDENTITY, generation, 0, deliveries())
    for table in ("stream.delivery", "stream.intake_event", "stream.quarantine", "ops.stream_batch"):
        assert con.execute(f"select count(*) from {table}").fetchone()[0] == 0


@pytest.mark.db
def test_checkpoint_lineage_rejects_topic_reset_or_missing_marker_and_metadata(registered):
    con, generation, checkpoint = registered
    assert lineage.register(con, DATASET, IDENTITY, checkpoint) == generation
    with pytest.raises(ValueError, match="marker does not match"):
        lineage.register(con, DATASET, replace(IDENTITY, topic_id="recreated-topic"), checkpoint)
    intake.persist_batch(con, DATASET, IDENTITY, generation, 0, deliveries())
    (checkpoint / "metadata").unlink()
    with pytest.raises(ValueError, match="metadata is missing"):
        lineage.register(con, DATASET, IDENTITY, checkpoint)
    checkpoint.with_name(checkpoint.name + ".lineage.json").unlink()
    with pytest.raises(ValueError, match="registered path"):
        lineage.register(con, DATASET, IDENTITY, checkpoint)


@pytest.mark.db
def test_stream_migrations_repeat_and_explicit_reset_includes_stream_schema(registered):
    con, _, _ = registered
    assert db.migrate(con, intake.MIGRATIONS) == []
    db.reset(con)
    assert con.execute("select 1 from pg_namespace where nspname='stream'").fetchone() is None
    assert len(db.migrate(con)) == 3
