"""Live SQL projections and reference/replay safety, independent of a Spark installation."""

from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace

import psycopg
import pytest
from conftest import write_arrival

from payrecon import core, db, generator, loader
from payrecon.streaming import intake, lineage, live
from payrecon.streaming.contract import ContractError, Dataset, decode, encode, utc
from payrecon.streaming.live_fixture import event, phase_records
from payrecon.streaming.progress import record_progress, status

pytestmark = pytest.mark.db
DATASET = Dataset("case03-intake-v1", utc("2026-10-01T00:00:00Z"), utc("2026-11-01T00:00:00Z"))
SOURCE = intake.Identity("cluster-live", "topic-live", "case03.live-check")


def records(phase):
    result = []
    for offset, (key, value) in enumerate(phase_records(phase)):
        try:
            decode(key, value, DATASET)
        except ContractError:
            continue
        result.append(intake.Delivery(0, offset, key, value, datetime(2026, 11, 12, tzinfo=UTC)))
    return result


@pytest.fixture
def prepared(con, tmp_path, monkeypatch):
    db.migrate(con, intake.MIGRATIONS)
    merchants = [
        generator.Merchant("M001", "GBP", 175, 25, datetime(2026, 9, 30)),
        generator.Merchant("M002", "EUR", 175, 25, datetime(2026, 9, 30)),
    ]
    late = generator.Merchant("M041", "GBP", 175, 25, datetime(2026, 10, 11, 18))
    monkeypatch.setattr(
        generator, "build_world", lambda seed: SimpleNamespace(merchants=merchants, late_merchants=[late])
    )
    live.load_reference(con, date(2026, 10, 2))
    checkpoint = tmp_path / "live"
    generation = lineage.register(con, DATASET, SOURCE, checkpoint, role="live")
    return con, generation, checkpoint


def fingerprint(con, generation):
    return {
        t: con.execute(
            f"select md5(coalesce(string_agg(t::text,'|' order by t::text),'')) from {t} t where generation=%s",
            [generation],
        ).fetchone()[0]
        for t in (
            "stream.live_event",
            "stream.event_application",
            "stream.payment_latest",
            "stream.event_minute",
        )
    }


def test_gaps_illegal_decisions_expiries_and_reference_refresh_revise_old_minutes(prepared):
    con, gen, _ = prepared
    live.persist_live(con, DATASET, SOURCE, gen, 0, records("initial"))
    assert con.execute(
        "select status,held_events,captured_minor from stream.payment_latest where payment_id='live-gap'"
    ).fetchone() == ("initiated", 1, 0)
    assert dict(
        con.execute("select outcome,count(*) from stream.event_application group by outcome")
    ) == dict(applied=7, held=1, illegal=1, unknown_reference=2)
    minute = "select decline_rate_pct from stream.operation_minute where minute_start='2026-10-13T10:01:00Z' and merchant_id='M001'"
    assert con.execute(minute).fetchone()[0] == 50
    live.persist_live(con, DATASET, SOURCE, gen, 1, records("complete"))
    assert con.execute(
        "select status,held_events,captured_minor from stream.payment_latest where payment_id='live-gap'"
    ).fetchone() == ("captured", 0, 4200)
    assert float(con.execute(minute).fetchone()[0]) == pytest.approx(100 / 3)
    live.load_reference(con, date(2026, 10, 12))
    assert float(con.execute(minute).fetchone()[0]) == pytest.approx(100 / 3)
    assert con.execute(
        "select authorised,declined,decline_rate_pct from stream.operation_minute where minute_start='2026-10-13T10:01:00Z' and merchant_id='M041'"
    ).fetchone() == (1, 0, 0)
    assert con.execute(
        "select expired,decline_rate_pct from stream.operation_minute where minute_start='2026-10-13T10:02:00Z'"
    ).fetchone() == (1, None)
    assert (
        con.execute(
            "select count(*) from stream.event_application where outcome='unknown_reference'"
        ).fetchone()[0]
        == 0
    )
    before = fingerprint(con, gen)
    assert live.load_reference(con, date(2026, 10, 12))["reused"]
    assert fingerprint(con, gen) == before
    with pytest.raises(ValueError, match="backwards"):
        live.load_reference(con, date(2026, 10, 2))


def test_replayed_callback_and_republished_events_preserve_canonical_and_projection_rows(prepared):
    con, gen, _ = prepared
    initial = records("initial")
    summary, _ = live.persist_live(con, DATASET, SOURCE, gen, 0, initial)
    before = fingerprint(con, gen)
    committed = con.execute("select committed_at from ops.stream_batch where query_role='live'").fetchone()
    replay, reused = live.persist_live(con, DATASET, SOURCE, gen, 0, initial)
    assert replay == summary and reused
    assert (
        con.execute("select committed_at from ops.stream_batch where query_role='live'").fetchone()
        == committed
    )
    live.persist_live(con, DATASET, SOURCE, gen, 1, initial)
    assert fingerprint(con, gen) == before
    with pytest.raises(intake.IntakeConflict, match="admitted set"):
        live.persist_live(con, DATASET, SOURCE, gen, 0, records("complete"))


@pytest.mark.parametrize("kind", ["event_id", "sequence"])
def test_conflicting_identity_is_archived_without_overwriting_live_rows(prepared, kind):
    con, gen, _ = prepared
    original = records("initial")[0]
    live.persist_live(con, DATASET, SOURCE, gen, 0, [original])
    changed, _ = decode(original.key, original.value, DATASET)
    changed["amount_minor"] += 1
    if kind == "sequence":
        changed["event_id"] = "other-event"
    result, _ = live.persist_live(
        con, DATASET, SOURCE, gen, 1, [replace(original, value=encode(changed, DATASET.dataset_id))]
    )
    assert result["blocking"] == 1
    assert con.execute("select count(*) from stream.live_conflict").fetchone()[0] == 1
    assert con.execute("select event->>'amount_minor' from stream.live_event").fetchone()[0] == "4200"
    with pytest.raises(intake.IntakeConflict):
        intake.halt_on_conflict(result)


def test_projection_failure_rolls_back_admission_and_batch_commit(prepared):
    con, gen, _ = prepared
    con.execute(
        "create function stream.inject_projection_failure() returns trigger language plpgsql as $$ begin raise exception 'injected projection failure'; end; $$"
    )
    con.execute(
        "create trigger failure before insert on stream.event_minute for each row execute function stream.inject_projection_failure()"
    )
    with pytest.raises(psycopg.errors.RaiseException, match="injected projection"):
        live.persist_live(con, DATASET, SOURCE, gen, 0, records("initial"))
    for table in (
        "stream.live_event",
        "stream.event_application",
        "stream.payment_latest",
        "stream.event_minute",
        "ops.stream_batch",
    ):
        assert con.execute(f"select count(*) from {table}").fetchone()[0] == 0


def test_roles_generations_and_reference_refresh_are_isolated(prepared, tmp_path):
    con, gen, path = prepared
    with pytest.raises(ValueError, match="marker does not match"):
        lineage.register(con, DATASET, SOURCE, path, role="intake")
    other = lineage.register(con, DATASET, SOURCE, tmp_path / "other-live", role="live")
    for generation in (gen, other):
        live.persist_live(con, DATASET, SOURCE, generation, 0, records("initial"))
    live.load_reference(con, date(2026, 10, 12))
    assert con.execute(
        "select generation,sum(event_count) from stream.event_minute group by generation order by generation"
    ).fetchall() == sorted([(__import__("uuid").UUID(gen), 11), (__import__("uuid").UUID(other), 11)])
    assert (
        con.execute(
            "select count(*) from stream.event_application where outcome='unknown_reference'"
        ).fetchone()[0]
        == 0
    )


def test_active_live_writer_fences_reference_changes_and_another_writer(prepared):
    con, _, _ = prepared
    live.lock_writer(con, session=True)
    try:
        with db.connect(con.info.dbname) as second:
            with pytest.raises(RuntimeError, match="another live writer"):
                live.lock_writer(second, session=True)
            with pytest.raises(RuntimeError, match="another live writer"):
                live.load_reference(second, date(2026, 10, 12))
    finally:
        con.execute("select pg_advisory_unlock_all()")


def test_sql_state_matches_batch_including_overrefund_and_reversal(prepared, tmp_path):
    con, gen, _ = prepared
    live.load_reference(con, date(2026, 10, 12))
    raw = [*records("initial"), *records("complete")]
    money = [
        event("money", 1, "initiated", 0),
        event("money", 2, "authorised", 1),
        event("money", 3, "captured", 2),
        event("money", 4, "refunded", 3, amount=4300),
        event("money", 5, "refunded", 4, amount=200),
        event("reversal", 1, "initiated", 0),
        event("reversal", 2, "authorised", 1),
        event("reversal", 3, "captured", 2),
        event("reversal", 4, "reversed", 3),
    ]
    raw += [
        intake.Delivery(0, 100 + i, e["payment_id"].encode(), encode(e, DATASET.dataset_id), None)
        for i, e in enumerate(money)
    ]
    live.persist_live(con, DATASET, SOURCE, gen, 0, raw)
    events = {e["event_id"]: e for r in raw for e, _ in [decode(r.key, r.value, DATASET)]}
    csv_events = [
        dict(
            e,
            event_time=e["event_time"].replace("T", " ").removesuffix("Z"),
            ingestion_time=e["ingestion_time"].replace("T", " ").removesuffix("Z"),
        )
        for e in events.values()
    ]
    merchant_rows = [
        dict(
            merchant_id=m,
            merchant_name=m,
            country="GB",
            currency="GBP",
            fee_bps=175,
            fee_fixed_minor=25,
            updated_at="2026-09-30 00:00:00",
        )
        for m in ("M001", "M041")
    ]
    folder = write_arrival(
        tmp_path / "arrival",
        "2026-10-14",
        {"merchants.csv": merchant_rows, "events_2026-10-13.csv": csv_events},
    )
    loader.load_arrival(con, folder)
    core.build(con, date(2026, 10, 14))
    cols = "payment_id,merchant_id,currency,status,last_sequence_no,held_events,initiated_at,captured_at,status_changed_at,captured_minor,refunded_minor,reversed_minor,expected_settlement_minor"
    assert (
        con.execute(f"select {cols} from stream.payment_latest order by payment_id").fetchall()
        == con.execute(f"select {cols} from core.payment order by payment_id").fetchall()
    )
    assert (
        con.execute(
            "select expected_settlement_minor from stream.payment_latest where payment_id='live-money'"
        ).fetchone()[0]
        == 4000
    )
    assert (
        con.execute(
            "select expected_settlement_minor from stream.payment_latest where payment_id='live-reversal'"
        ).fetchone()[0]
        == 0
    )


def test_currency_identity_mismatch_does_not_mix_money_or_decisions(prepared):
    con, gen, _ = prepared
    e = event("gap", 2, "authorised", 1)
    e["currency"] = "EUR"
    live.persist_live(
        con,
        DATASET,
        SOURCE,
        gen,
        0,
        [records("initial")[0], intake.Delivery(0, 100, b"live-gap", encode(e, DATASET.dataset_id), None)],
    )
    assert con.execute("select count(*) from stream.payment_latest").fetchone()[0] == 0
    assert dict(con.execute("select outcome,count(*) from stream.event_application group by outcome")) == {
        "identity_mismatch": 2
    }
    assert all(r[0] is None for r in con.execute("select decline_rate_pct from stream.operation_minute"))


def test_frontier_status_is_explicitly_provisional_until_intake_reference_and_hashes_agree(
    prepared, tmp_path
):
    con, gen, _ = prepared
    reader = lineage.register(con, DATASET, SOURCE, tmp_path / "intake")
    source_records = records("initial")
    live.persist_live(con, DATASET, SOURCE, gen, 0, source_records)
    end = {SOURCE.topic: {"0": 12, "1": 0, "2": 0}}
    progress = dict(batchId=0, sources=[dict(endOffset=end)])
    record_progress(con, gen, "live", progress)
    assert not status(con, gen)["consistent_at_recorded_frontier"]
    intake.persist_batch(con, DATASET, SOURCE, reader, 0, source_records)
    record_progress(con, reader, "intake", progress)
    assert not status(con, gen)["consistent_at_recorded_frontier"]  # Unknown reference still explicit.
    live.load_reference(con, date(2026, 10, 12))
    assert status(con, gen)["consistent_at_recorded_frontier"]
    live.persist_live(con, DATASET, SOURCE, gen, 1, source_records)
    assert not status(con, gen)["progress_covers_commits"]
    assert not status(con, gen)["consistent_at_recorded_frontier"]
    record_progress(con, gen, "live", dict(progress, batchId=1))
    assert status(con, gen)["consistent_at_recorded_frontier"]
    extra = records("late")
    extra = [replace(extra[0], offset=100)]
    intake.persist_batch(con, DATASET, SOURCE, reader, 1, extra)
    assert status(con, gen)["intake_events_not_admitted"] == 1
    assert not status(con, gen)["consistent_at_recorded_frontier"]
