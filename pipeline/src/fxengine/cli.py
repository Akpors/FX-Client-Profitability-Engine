"""Command-line entry point for the fxengine pipeline.

Each subcommand is a stub that returns 0 until the task that implements it wires it up.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def _stub(name: str):
    def handler(args: argparse.Namespace) -> int:
        print(f"fxengine {name}: not implemented yet")
        return 0

    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fxengine",
        description="Synthetic spot-FX trade book pipeline: ECB rates -> generate -> load.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    for name, help_text in (
        ("fetch-ecb", "Download ECB reference rates and write the CSV snapshot"),
        ("generate", "Generate the synthetic client book and trade book"),
        ("load", "Load generated tables into the Postgres raw schema"),
        ("param-sheet", "Render docs/methodology/parameter-sheet.md"),
    ):
        sub.add_parser(name, help=help_text).set_defaults(handler=_stub(name))

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
