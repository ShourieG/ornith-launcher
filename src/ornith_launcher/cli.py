"""Command-line interface."""

from __future__ import annotations

import argparse

from ornith_launcher import __version__

PROG = "ornith-launcher"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Unofficial launcher for running Ornith models locally with llama.cpp.",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__} (unofficial)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
