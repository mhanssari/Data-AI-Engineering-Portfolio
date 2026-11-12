"""Repair eligibility, exact frontier/identity proof, atomic publication and replay safety."""

from datetime import date

import psycopg
import pytest
from test_stream_live import DATASET, SOURCE
from test_stream_live import prepared as prepared

from payrecon import db
from payrecon.streaming import intake, lineage, live, recovery
from payrecon.streaming.contract import ContractError, decode
from payrecon.streaming.faults import validate_crash
from payrecon.streaming.live_fixture import phase_records
from payrecon.streaming.progress import record_progress, status


@pytest.fixture
def repairable(prepared, tmp_path):
    con, generation, _ = prepared
    live.load_reference(con, date(2026, 10, 12))
    reader = lineage.register(con, DATASET, SOURCE, tmp_path / "intake")
    raw = [
        intake.Delivery(0, i, key, value, None)
        for i, (key, value) in enumerate(
            [*phase_records("initial"), *phase_records("advance"), *phase_records("late-gap")]
        )
    ]
    intake.persist_batch(con, DATASET, SOURCE, reader, 0, raw)
    admitted = []
    for row in raw[:-1]:
        try:
            decode(row.key, row.value, DATASET)
        except ContractError:
            continue
        admitted.append(row)
    live.persist_live(con, DATASET, SOURCE, generation, 0, admitted)
    progress = dict(batchId=0, sources=[dict(endOffset={SOURCE.topic: {"0": len(raw)}})])
    for gen, role in ((reader, "intake"), (generation, "live")):
        record_progress(con, gen, role, progress)
    return con, generation, recovery.plan(con, generation), reader


@pytest.mark.db
def test_late_authorisation_repair_rewalks_history_preserves_old_events_and_is_idempotent(repairable):
    con, gen, spec, _ = repairable
    old = con.execute("select event_id,first_commit_at from stream.live_event order by event_id").fetchall()
    assert not status(con, gen)["consistent_at_recorded_frontier"]
    result, reused = recovery.backfill(con, spec, "repair-1", "recover the late authorisation")
    assert not reused and result["added_events"] == 1 and result["source_events"] == 13
    assert con.execute(
        "select status,held_events,captured_minor from stream.payment_latest where payment_id='live-gap'"
    ).fetchone() == ("captured", 0, 4200)
    assert (
        con.execute("select admission_origin from stream.live_event where event_id='live-gap-2'").fetchone()[
            0
        ]
        == "backfill"
    )
    assert (
        con.execute(
            "select event_id,first_commit_at from stream.live_event where event_id<>'live-gap-2' order by event_id"
        ).fetchall()
        == old
    )
    assert status(con, gen)["consistent_at_recorded_frontier"]
    assert status(con, gen)["last_repair"]["repair_id"] == "repair-1"
    before = recovery.snapshot(con, gen)
    original_audit = con.execute("select completed_at from ops.stream_backfill").fetchone()
    repeated, reused = recovery.backfill(con, spec, "repair-1", "recover the late authorisation")
    assert reused and repeated == result
    assert recovery.snapshot(con, gen) == before
    assert con.execute("select completed_at from ops.stream_backfill").fetchone() == original_audit
    with pytest.raises(ValueError, match="different request"):
        recovery.backfill(con, spec, "repair-1", "another reason")
    with pytest.raises(ValueError, match="version"):
        recovery.backfill(con, dict(spec, version=True), "typed-version", "must not coerce a boolean")
    changed_reference = dict(spec["reference"], seed=float(spec["reference"]["seed"]))
    with pytest.raises(ValueError, match="stale"):
        recovery.backfill(
            con, dict(spec, reference=changed_reference), "typed-reference", "must not coerce a float"
        )


@pytest.mark.db
@pytest.mark.parametrize("changed", ["reference", "frontier", "identity"])
def test_stale_or_changed_plan_never_mutates_live_history(repairable, changed):
    con, gen, spec, reader = repairable
    before = recovery.snapshot(con, gen)
    if changed == "reference":
        live.load_reference(con, date(2026, 10, 13))
    elif changed == "frontier":
        for generation, role in ((reader, "intake"), (gen, "live")):
            con.execute(
                "insert into ops.stream_batch values (%s,%s,1,%s,%s,now())", [generation, role, "hash", "{}"]
            )
            record_progress(
                con, generation, role, dict(batchId=1, sources=[dict(endOffset={SOURCE.topic: {"0": 17}})])
            )
    else:
        spec = dict(spec, topic_id="recreated-topic")
    with pytest.raises(ValueError, match="stale"):
        recovery.backfill(con, spec, "stale", "must refuse")
    assert recovery.snapshot(con, gen) == before
    assert con.execute("select count(*) from ops.stream_backfill").fetchone()[0] == 0


@pytest.mark.db
@pytest.mark.parametrize("damage", ["hole", "beyond", "canonical", "conflict"])
def test_missing_delivery_or_untrusted_history_prevents_repair(repairable, damage):
    con, gen, spec, _ = repairable
    before = recovery.snapshot(con, gen)
    if damage == "hole":
        con.execute('delete from stream.delivery where "offset"=0')
    elif damage == "beyond":
        con.execute(
            'insert into stream.delivery(cluster_id,topic_id,partition,"offset",topic) values (%s,%s,0,16,%s)',
            [SOURCE.cluster_id, SOURCE.topic_id, SOURCE.topic],
        )
    elif damage == "canonical":
        con.execute("delete from stream.intake_event where event_id='live-gap-2'")
    else:
        con.execute(
            "insert into stream.live_conflict values (%s,99,'bad','hash','{}','test conflict')", [gen]
        )
    with pytest.raises(ValueError, match="prefix|canonical|conflict"):
        recovery.backfill(con, spec, "untrusted", "must refuse")
    assert recovery.snapshot(con, gen) == before


@pytest.mark.db
@pytest.mark.parametrize("writer", ["live", "intake"])
def test_both_active_readers_are_fenced_before_repair(repairable, writer):
    con, _, spec, _ = repairable
    key = live.LOCK if writer == "live" else "payrecon/intake/" + DATASET.dataset_id
    con.execute("select pg_advisory_lock(hashtextextended(%s,0))", [key])
    try:
        with db.connect(con.info.dbname) as other, pytest.raises(RuntimeError, match="writer"):
            recovery.backfill(other, spec, "fenced", "must wait for paused readers")
    finally:
        con.execute("select pg_advisory_unlock_all()")


@pytest.mark.db
def test_failure_writing_repair_audit_rolls_back_admission_and_all_projections(repairable):
    con, gen, spec, _ = repairable
    before = recovery.snapshot(con, gen)
    con.execute(
        "create function stream.fail_repair() returns trigger language plpgsql as $$ begin raise exception 'injected audit failure'; end; $$"
    )
    con.execute(
        "create trigger failure before insert on ops.stream_backfill for each row execute function stream.fail_repair()"
    )
    with pytest.raises(psycopg.errors.RaiseException, match="injected audit"):
        recovery.backfill(con, spec, "atomic", "prove rollback")
    assert recovery.snapshot(con, gen) == before
    assert con.execute("select count(*) from ops.stream_backfill").fetchone()[0] == 0
    con.execute("drop trigger failure on ops.stream_backfill")
    assert recovery.backfill(con, spec, "atomic", "prove rollback")[0]["added_events"] == 1


@pytest.mark.parametrize(
    "database,role,topic",
    [
        ("payments_stream", "live", "case03.live-check-abcd"),
        ("payments_stream_check_0123456789ab", "intake", "case03.live-check-abcd"),
        ("payments_stream_check_0123456789ab", "live", "case03.payment-events.v1"),
    ],
)
def test_abrupt_exit_option_refuses_retained_or_non_diagnostic_resources(monkeypatch, database, role, topic):
    monkeypatch.setenv("PGDATABASE", database)
    with pytest.raises(ValueError, match="disposable"):
        validate_crash("before-commit", role, topic)
