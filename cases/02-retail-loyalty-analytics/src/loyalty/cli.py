"""Command line: `loyalty generate | load | build | run-all`."""

import argparse
import sys
from pathlib import Path

from loyalty import generator, loader, transform
from loyalty.config import BATCH_WINDOWS, SEED, data_dir, warehouse_path


def _load_batches(batches: list[int], data: Path, warehouse: Path) -> None:
    con = loader.connect(warehouse)
    try:
        for batch in batches:
            results = loader.load_batch(con, data / f"batch_{batch}")
            landed = sum(r.rows for r in results)
            skipped = sum(r.skipped for r in results)
            print(
                f"batch {batch}: {landed} rows landed, {skipped} of {len(results)} files already loaded",
                flush=True,
            )
    finally:
        # DuckDB allows one writer: release the file before dbt opens it.
        con.close()


def _generate(args: argparse.Namespace) -> int:
    for manifest in generator.generate(args.out, args.seed):
        print(f"wrote {manifest.parent}")
    return 0


def _load(args: argparse.Namespace) -> int:
    batches = sorted(BATCH_WINDOWS) if args.batch == "all" else [int(args.batch)]
    _load_batches(batches, args.data, args.warehouse)
    return 0


def _build(args: argparse.Namespace) -> int:
    transform.run_dbt(["build"], args.warehouse)
    return 0


def _run_all(args: argparse.Namespace) -> int:
    """Generate, then load and build one batch at a time, so the snapshot sees each batch's state in order."""
    if args.warehouse.exists():
        args.warehouse.unlink()
        print(f"removed {args.warehouse}", flush=True)
    generator.generate(args.data, args.seed)
    for batch in sorted(BATCH_WINDOWS):
        _load_batches([batch], args.data, args.warehouse)
        transform.run_dbt(["source", "freshness"], args.warehouse)
        transform.run_dbt(["build"], args.warehouse)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loyalty", description="Retail loyalty analytics pipeline")
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str, handler) -> argparse.ArgumentParser:
        command = commands.add_parser(name, help=help_text)
        command.set_defaults(handler=handler)
        return command

    gen = add("generate", "write both batch extracts from the seeded generator", _generate)
    gen.add_argument("--seed", type=int, default=SEED)
    gen.add_argument("--out", type=Path, default=data_dir())

    load = add("load", "land batch extracts in the raw schema", _load)
    load.add_argument("--batch", choices=[*map(str, sorted(BATCH_WINDOWS)), "all"], default="all")
    load.add_argument("--data", type=Path, default=data_dir())
    load.add_argument("--warehouse", type=Path, default=warehouse_path())

    build = add("build", "dbt build: models, snapshot and tests", _build)
    build.add_argument("--warehouse", type=Path, default=warehouse_path())

    run_all = add(
        "run-all", "from an empty warehouse: generate, then load and build each batch in order", _run_all
    )
    run_all.add_argument("--seed", type=int, default=SEED)
    run_all.add_argument("--data", type=Path, default=data_dir())
    run_all.add_argument("--warehouse", type=Path, default=warehouse_path())

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (loader.LoadError, transform.DbtError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
