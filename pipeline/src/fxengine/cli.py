"""Command-line entry point for the fxengine pipeline.

``fetch-ecb``, ``run-all`` and ``param-sheet`` are real. ``generate`` and ``load`` are stubs that
return 0 until the task that implements them wires them up (``run-all`` does what ``generate``
and ``load`` would, in one go).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import psycopg
import sqlalchemy.exc
import yaml

from fxengine import ecb, param_sheet
from fxengine.config import DEFAULT_CONFIG_PATH, load_config
from fxengine.load import LoadError, assemble_raw_tables, engine_from_env, write_raw


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


def _run_all(args: argparse.Namespace) -> int:
    """Config, snapshot, clients, trades, reference tables, then the raw load.

    The window comes from the snapshot's file name, never from today's date, so the same config
    and snapshot always give the same tables. ``--dry-run`` stops before the database.
    """
    started = time.perf_counter()
    try:
        snapshot = args.snapshot or ecb.latest_snapshot(ecb.DEFAULT_SNAPSHOT_DIR)
        window = ecb.window_from_snapshot(snapshot)
        cfg = load_config(args.config)
        rates = ecb.read_snapshot(snapshot)
        print(
            f"fxengine run-all: config {args.config}, snapshot {Path(snapshot).name}, "
            f"window {window[0]} to {window[1]}"
        )
        tables = assemble_raw_tables(cfg, rates, window)
        generated = time.perf_counter()
        print(f"fxengine run-all: generated {len(tables)} tables in {generated - started:.1f}s")
        if args.dry_run:
            counts = {name: len(frame) for name, frame in tables.items()}
        else:
            counts = write_raw(engine_from_env(), tables)
            loaded = time.perf_counter() - generated
            print(f"fxengine run-all: loaded into Postgres in {loaded:.1f}s")
    except (OSError, ValueError, ecb.EcbError, LoadError) as exc:
        # ValueError covers a bad config (pydantic's ValidationError) and a bad snapshot name.
        print(f"fxengine run-all: error: {' '.join(str(exc).split())}", file=sys.stderr)
        return 1
    except (psycopg.Error, sqlalchemy.exc.SQLAlchemyError) as exc:
        message = " ".join(str(getattr(exc, "orig", None) or exc).split())
        print(f"fxengine run-all: database error: {message}", file=sys.stderr)
        return 1

    for name, count in counts.items():
        print(f"  raw.{name:<18} {count:>9,} rows")
    action = "dry run, nothing written to Postgres" if args.dry_run else "raw schema replaced"
    print(f"fxengine run-all: {action} ({time.perf_counter() - started:.1f}s total)")
    return 0


def _param_sheet(args: argparse.Namespace) -> int:
    """Render the calibration parameter sheet from the config and the rationale file.

    The sheet is built in memory first, so a bad config or a missing rationale key leaves any
    existing sheet untouched.
    """
    try:
        cfg = load_config(args.config)
        rationale = param_sheet.load_rationale(args.rationale)
        sheet = param_sheet.render_parameter_sheet(cfg, rationale)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(sheet, encoding="utf-8")
    except KeyError as exc:
        print(f"fxengine param-sheet: error: {exc.args[0]}", file=sys.stderr)
        return 1
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # ValueError covers a bad config (pydantic's ValidationError) and a bad rationale file.
        print(f"fxengine param-sheet: error: {' '.join(str(exc).split())}", file=sys.stderr)
        return 1
    print(f"fxengine param-sheet: wrote {args.out} ({len(sheet.splitlines())} lines)")
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

    run_all = sub.add_parser(
        "run-all", help="Generate every raw table from the ECB snapshot and load it into Postgres"
    )
    run_all.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Generator config (default: %(default)s)",
    )
    run_all.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="ECB snapshot CSV; its file name sets the window "
        "(default: the latest in the committed snapshot directory)",
    )
    run_all.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate everything and print the row counts, but do not touch Postgres",
    )
    run_all.set_defaults(handler=_run_all)

    sheet = sub.add_parser(
        "param-sheet",
        help="Render the calibration parameter sheet (docs/methodology/parameter-sheet.md)",
    )
    sheet.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Generator config (default: %(default)s)",
    )
    sheet.add_argument(
        "--rationale",
        type=Path,
        default=param_sheet.DEFAULT_RATIONALE_PATH,
        help="One-line rationale for each parameter (default: %(default)s)",
    )
    sheet.add_argument(
        "--out",
        type=Path,
        default=param_sheet.DEFAULT_SHEET_PATH,
        help="Where to write the sheet (default: %(default)s)",
    )
    sheet.set_defaults(handler=_param_sheet)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
