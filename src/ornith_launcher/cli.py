"""Command-line interface."""

from __future__ import annotations

import argparse
import logging
import sys

from ornith_launcher import __version__, scan
from ornith_launcher.app import App
from ornith_launcher.errors import OrnithLauncherError

PROG = "ornith-launcher"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Unofficial launcher for running Ornith models locally with llama.cpp.",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__} (unofficial)")
    parser.add_argument("--verbose", action="store_true", help="log details to stderr")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    scan_parser = commands.add_parser(
        "scan", help="show which Ornith models fit this machine (read-only)"
    )
    scan.add_arguments(scan_parser)
    scan_parser.set_defaults(handler=scan.run)
    return parser


def main(argv: list[str] | None = None, app: App | None = None) -> int:
    """Run the CLI and return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    app = app or App.default()
    try:
        return handler(app, args)
    except OrnithLauncherError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        if exc.hint:
            print(exc.hint, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
