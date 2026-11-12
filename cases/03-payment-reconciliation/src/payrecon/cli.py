"""Command line: `payrecon generate | migrate | reset | load | build-core`."""

import argparse
import sys
from datetime import date
from pathlib import Path

from payrecon import core, db, generator, loader
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

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except loader.LoadError as error:
        print(f"load rejected: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
