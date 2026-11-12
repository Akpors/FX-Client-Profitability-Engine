"""Command-line entry point for the fxengine pipeline.

Subcommands that are not built yet are stubs that return 0 until the task that implements them
wires them up.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from fxengine import ecb


def _stub(name: str):
    def handler(args: argparse.Namespace) -> int:
        print(f"fxengine {name}: not implemented yet")
        return 0

    return handler


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a date, expected YYYY-MM-DD") from None


def _fetch_ecb(args: argparse.Namespace) -> int:
    build_date = args.build_date or date.today()
    try:
        path = ecb.refresh_snapshot(build_date, args.snapshot_dir)
    except ecb.EcbError as exc:
        # One line naming the cause. Nothing was written: refresh_snapshot only writes after
        # the download has succeeded and validated.
        print(f"fxengine fetch-ecb: error: {' '.join(str(exc).split())}", file=sys.stderr)
        return 1
    df = ecb.read_snapshot(path)
    print(
        f"fxengine fetch-ecb: wrote {path} "
        f"({len(df)} rows, {df['currency'].nunique()} currencies, "
        f"{df['rate_date'].min()} to {df['rate_date'].max()})"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fxengine",
        description="Synthetic spot-FX trade book pipeline: ECB rates -> generate -> load.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    for name, help_text in (
        ("generate", "Generate the synthetic client book and trade book"),
        ("load", "Load generated tables into the Postgres raw schema"),
        ("param-sheet", "Render docs/methodology/parameter-sheet.md"),
    ):
        sub.add_parser(name, help=help_text).set_defaults(handler=_stub(name))

    fetch = sub.add_parser(
        "fetch-ecb", help="Download ECB reference rates and write the CSV snapshot"
    )
    fetch.add_argument(
        "--build-date",
        type=_iso_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Window is the 24 complete months before this date (default: today)",
    )
    fetch.add_argument(
        "--snapshot-dir",
        type=Path,
        default=ecb.DEFAULT_SNAPSHOT_DIR,
        help="Where to write the snapshot (default: %(default)s)",
    )
    fetch.set_defaults(handler=_fetch_ecb)

    run_all = sub.add_parser("run-all", help="Run generate and load from the committed snapshot")
    run_all.add_argument(
        "--dry-run", action="store_true", help="Validate inputs and stop before writing anything"
    )
    run_all.set_defaults(handler=_stub("run-all"))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
