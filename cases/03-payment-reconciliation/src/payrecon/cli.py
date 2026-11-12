"""Command line: `payrecon generate | migrate | reset | load | build-core | reconcile | run-all`."""

import argparse
import sys
from datetime import date
from pathlib import Path

from payrecon import core, db, generator, loader, recon
from payrecon.config import SEED, arrival_days, data_dir


def _generate(args: argparse.Namespace) -> int:
    manifests = generator.generate(args.out, args.seed)
    print(f"wrote {len(manifests)} arrival folders to {args.out}", flush=True)
    return 0


def _migrate(args: argparse.Namespace) -> int:
    with db.connect() as con:
        applied = db.migrate(con)
    print(f"applied {len(applied)} migrations: {', '.join(applied) or 'none'}", flush=True)
    return 0


def _reset(args: argparse.Namespace) -> int:
    if not args.yes:
        print("reset drops every schema this case owns; pass --yes", file=sys.stderr)
        return 2
    with db.connect() as con:
        db.reset(con)
        db.migrate(con)
    print("reset done", flush=True)
    return 0


def _load(args: argparse.Namespace) -> int:
    days = arrival_days() if args.arrival == "all" else [date.fromisoformat(args.arrival)]
    with db.connect() as con:
        for day in days:
            results = loader.load_arrival(con, args.data / f"arrival_{day}")
            loaded = sum(r.loaded for r in results)
            quarantined = sum(r.quarantined for r in results)
            skipped = sum(r.skipped for r in results)
            print(
                f"arrival {day}: {loaded} rows loaded, {quarantined} quarantined, "
                f"{skipped} of {len(results)} files already loaded",
                flush=True,
            )
    return 0


def _build_core(args: argparse.Namespace) -> int:
    with db.connect() as con:
        as_of = date.fromisoformat(args.arrival) if args.arrival else core.latest_arrival(con)
        if as_of is None:
            print("nothing has been loaded yet; run payrecon load first", file=sys.stderr)
            return 1
        counts = core.build(con, as_of)
    print(
        f"core as of arrival {as_of}: " + ", ".join(f"{n} {t.split('.')[1]}" for t, n in counts.items()),
        flush=True,
    )
    return 0


def _print_run(as_of: date, summary: dict) -> None:
    payments = ", ".join(f"{n} {s}" for s, n in summary["payments"].items())
    lines = ", ".join(f"{n} {s}" for s, n in summary["unmatched_lines"].items()) or "none"
    print(
        f"run as of arrival {as_of}: payments {payments}; unmatched lines {lines}; "
        f"{summary['open_exceptions']} open exceptions; checks passed",
        flush=True,
    )


def _reconcile(args: argparse.Namespace) -> int:
    with db.connect() as con:
        as_of = date.fromisoformat(args.arrival) if args.arrival else core.latest_arrival(con)
        if as_of is None:
            print("nothing has been loaded yet; run payrecon load first", file=sys.stderr)
            return 1
        _print_run(as_of, recon.reconcile(con, as_of))
    return 0


def _run_all(args: argparse.Namespace) -> int:
    """Replay the schedule: each arrival day in order, load it, then reconcile as of it."""
    with db.connect() as con:
        for day in arrival_days():
            loader.load_arrival(con, args.data / f"arrival_{day}")
            _print_run(day, recon.reconcile(con, day))
    return 0


def _stream_prepare(args: argparse.Namespace) -> int:
    from payrecon.streaming.intake import prepare

    print("stream migrations: " + ", ".join(prepare()))
    return 0


def _stream_produce(args: argparse.Namespace) -> int:
    import json

    from payrecon.streaming.producer import produce_fixture

    print(json.dumps(produce_fixture(args.topic, args.dataset)), flush=True)
    return 0


def _stream_intake(args: argparse.Namespace) -> int:
    from payrecon.streaming.contract import Dataset, utc
    from payrecon.streaming.runtime import run_query

    run_query(
        Dataset(args.dataset, utc(args.source_start), utc(args.source_end)),
        args.topic,
        args.checkpoint,
        role=args.role,
        crash_at=getattr(args, "crash_at", None),
        available_now=args.available_now,
    )
    return 0


def _stream_reference(args: argparse.Namespace) -> int:
    import json

    from payrecon.streaming.live import load_reference

    with db.connect() as con:
        print(json.dumps(load_reference(con, date.fromisoformat(args.arrival))))
    return 0


def _stream_status(args: argparse.Namespace) -> int:
    import json

    from payrecon.streaming.progress import status

    with db.connect() as con:
        print(json.dumps(status(con, args.generation)))
    return 0


def _stream_live_fixture(args: argparse.Namespace) -> int:
    import json

    from payrecon.streaming.live_fixture import produce_phase

    print(json.dumps(produce_phase(args.topic, args.phase, args.dataset)))
    return 0


def _stream_recovery_plan(args: argparse.Namespace) -> int:
    import json

    from payrecon.streaming.recovery import plan

    with db.connect() as con:
        print(json.dumps(plan(con, args.generation), indent=2))
    return 0


def _stream_backfill(args: argparse.Namespace) -> int:
    import json

    from payrecon.streaming.recovery import backfill

    spec = json.loads(args.plan.read_text(encoding="utf-8"))
    with db.connect() as con:
        summary, reused = backfill(con, spec, args.repair_id, args.reason)
        print(json.dumps(dict(repair_id=args.repair_id, reused=reused, **summary)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="payrecon", description="Payment settlement reconciliation")
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str, handler) -> argparse.ArgumentParser:
        command = commands.add_parser(name, help=help_text)
        command.set_defaults(handler=handler)
        return command

    gen = add("generate", "write every arrival folder from the seeded generator", _generate)
    gen.add_argument("--seed", type=int, default=SEED)
    gen.add_argument("--out", type=Path, default=data_dir())

    add("migrate", "apply database migrations", _migrate)

    reset = add("reset", "drop and recreate the case's schemas", _reset)
    reset.add_argument("--yes", action="store_true")

    load = add("load", "land one arrival folder (or all) in raw", _load)
    load.add_argument("--arrival", default="all", help="YYYY-MM-DD or all")
    load.add_argument("--data", type=Path, default=data_dir())

    build_core = add("build-core", "rebuild core from everything arrived by a date", _build_core)
    build_core.add_argument("--arrival", help="YYYY-MM-DD; default the latest arrival loaded")

    reconcile = add("reconcile", "build core and reconcile as of an arrival date", _reconcile)
    reconcile.add_argument("--arrival", help="YYYY-MM-DD; default the latest arrival loaded")

    run_all = add("run-all", "load and reconcile every arrival day in order", _run_all)
    run_all.add_argument("--data", type=Path, default=data_dir())

    add("stream-prepare", "create/migrate a separate payments_stream* database", _stream_prepare)
    produce = add(
        "stream-produce-fixture", "publish eight acknowledged intake-check deliveries", _stream_produce
    )
    live_fixture = add(
        "stream-produce-live-fixture", "publish a bounded live-check fixture phase", _stream_live_fixture
    )
    live_fixture.add_argument(
        "--phase", choices=("initial", "complete", "repeat", "advance", "late", "late-gap"), required=True
    )
    live_fixture.add_argument("--topic", default="case03.live-check")
    live_fixture.add_argument("--dataset", default="case03-intake-v1")
    intake = add(
        "stream-intake", "archive Kafka deliveries and canonical events without a watermark", _stream_intake
    )
    intake.set_defaults(role="intake")
    live = add("stream-live", "validate, watermark and project operational payment events", _stream_intake)
    live.set_defaults(role="live")
    live.add_argument(
        "--crash-at",
        choices=("before-commit", "after-commit"),
        help="abrupt exit on first non-empty callback; disposable checks only",
    )
    reference = add(
        "stream-reference", "load/refresh the independent seeded reference snapshot", _stream_reference
    )
    reference.add_argument("--arrival", default="2026-10-02")
    status = add("stream-status", "inspect one live generation at reported query frontiers", _stream_status)
    status.add_argument("--generation", required=True)
    repair_plan = add(
        "stream-backfill-plan",
        "capture explicit intake/reference scope for a bounded repair",
        _stream_recovery_plan,
    )
    repair_plan.add_argument("--generation", required=True)
    repair = add(
        "stream-backfill",
        "repair a paused live generation from a reviewed intake-prefix plan",
        _stream_backfill,
    )
    repair.add_argument("--plan", type=Path, required=True)
    repair.add_argument("--repair-id", required=True)
    repair.add_argument("--reason", required=True)
    for command in (produce, intake, live):
        command.add_argument("--topic", default="case03.intake-check")
        command.add_argument("--dataset", default="case03-intake-v1")
    for command in (intake, live):
        command.add_argument("--source-start", default="2026-10-01T00:00:00Z")
        command.add_argument("--source-end", default="2026-11-01T00:00:00Z")
        command.add_argument(
            "--checkpoint", type=Path, default=Path(f"/state/{command.get_default('role')}-v1")
        )
        command.add_argument(
            "--available-now", action="store_true", help="drain current offsets and stop; default continuous"
        )

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except loader.LoadError as error:
        print(f"load rejected: {error}", file=sys.stderr)
        return 1
    except recon.CheckFailed as error:
        print(f"reconciliation checks failed, results rolled back: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
