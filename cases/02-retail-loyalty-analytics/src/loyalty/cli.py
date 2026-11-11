"""Command line: `loyalty generate`, `loyalty load`."""

import argparse
import sys
from pathlib import Path

from loyalty import generator, loader
from loyalty.config import BATCH_WINDOWS, SEED, data_dir, warehouse_path


def _generate(args: argparse.Namespace) -> int:
    for manifest in generator.generate(args.out, args.seed):
        print(f"wrote {manifest.parent}")
    return 0


def _load(args: argparse.Namespace) -> int:
    batches = sorted(BATCH_WINDOWS) if args.batch == "all" else [int(args.batch)]
    con = loader.connect(args.warehouse)
    try:
        for batch in batches:
            results = loader.load_batch(con, args.data / f"batch_{batch}")
            landed = sum(r.rows for r in results)
            skipped = sum(r.skipped for r in results)
            print(f"batch {batch}: {landed} rows landed, {skipped} of {len(results)} files already loaded")
    except loader.LoadError as error:
        print(f"load rejected: {error}", file=sys.stderr)
        return 1
    finally:
        con.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loyalty", description="Retail loyalty analytics pipeline")
    commands = parser.add_subparsers(dest="command", required=True)

    gen = commands.add_parser("generate", help="write both batch extracts from the seeded generator")
    gen.add_argument("--seed", type=int, default=SEED)
    gen.add_argument("--out", type=Path, default=data_dir())
    gen.set_defaults(handler=_generate)

    load = commands.add_parser("load", help="land batch extracts in the raw schema")
    load.add_argument("--batch", choices=[*map(str, sorted(BATCH_WINDOWS)), "all"], default="all")
    load.add_argument("--data", type=Path, default=data_dir())
    load.add_argument("--warehouse", type=Path, default=warehouse_path())
    load.set_defaults(handler=_load)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
