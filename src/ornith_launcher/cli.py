"""Command-line interface."""

from __future__ import annotations

import argparse
import logging

from ornith_launcher import __version__, doctor, launch, scan, uninstall, wizard
from ornith_launcher.app import App
from ornith_launcher.bench import run as bench
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

    setup_parser = commands.add_parser("setup", help="choose a model, download it and set it up")
    wizard.add_arguments(setup_parser)
    setup_parser.set_defaults(handler=wizard.run_setup)

    launch_parser = commands.add_parser("launch", help="start a profile's model server")
    launch.add_launch_arguments(launch_parser)
    launch_parser.set_defaults(handler=launch.run_launch)

    stop_parser = commands.add_parser("stop", help="stop a model server and free its memory")
    launch.add_name_argument(stop_parser)
    stop_parser.set_defaults(handler=launch.run_stop)

    status_parser = commands.add_parser("status", help="what is running, and the models folder")
    launch.add_name_argument(status_parser)
    status_parser.add_argument(
        "--command", dest="verbose_command", action="store_true", help="show the docker command"
    )
    status_parser.set_defaults(handler=launch.run_status)

    list_parser = commands.add_parser("list", help="list profiles")
    list_parser.set_defaults(handler=launch.run_list)

    monitor_parser = commands.add_parser("monitor", help="watch a running server")
    launch.add_name_argument(monitor_parser)
    monitor_parser.set_defaults(handler=launch.run_monitor)

    remove_parser = commands.add_parser(
        "remove", help="remove a profile (asks before deleting files)"
    )
    launch.add_name_argument(remove_parser, required=True)
    remove_parser.add_argument("--yes", action="store_true", help="accept the defaults")
    remove_parser.set_defaults(handler=launch.run_remove)

    uninstall_parser = commands.add_parser(
        "uninstall", help="undo everything ornith-launcher created"
    )
    uninstall_parser.add_argument("--yes", action="store_true", help="accept the defaults")
    uninstall_parser.set_defaults(handler=uninstall.run_uninstall)

    doctor_parser = commands.add_parser(
        "doctor", help="check everything the launcher needs (read-only)"
    )
    doctor_parser.set_defaults(handler=doctor.run_doctor)

    bench_parser = commands.add_parser(
        "bench", help="needle and perplexity tests (long GPU runs; pauses when hot)"
    )
    bench.add_arguments(bench_parser)
    bench_parser.set_defaults(handler=bench.run_bench)
    return parser


def main(argv: list[str] | None = None, app: App | None = None) -> int:
    """Run the CLI and return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    app = app or App.default()
    handler = getattr(args, "handler", None) or (lambda app, args: launch.default_action(app))
    try:
        return handler(app, args)
    except OrnithLauncherError as exc:
        app.warn(f"{PROG}: {exc}")
        if exc.hint:
            app.warn(exc.hint)
        return 1
    except KeyboardInterrupt:
        app.warn("")
        return 130
