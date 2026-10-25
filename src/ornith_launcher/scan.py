"""The ``scan`` command: what this machine can run, read-only.

It prints the system check, the suggested models and, with ``--full``, every
combination with its fit verdict. ``--refresh`` rescans Hugging Face first.
"""

from __future__ import annotations

import argparse
import datetime
from dataclasses import dataclass

from ornith_launcher.app import App
from ornith_launcher.catalog.catalog import (
    Catalog,
    CatalogBuilder,
    Entry,
    ScanProgress,
    entries,
    load_catalog,
    load_for_refresh,
    save_catalog,
)
from ornith_launcher.catalog.classify import Rules, load_rules
from ornith_launcher.catalog.families import Families, load_families
from ornith_launcher.catalog.hfapi import HubClient
from ornith_launcher.pins import load_pins
from ornith_launcher.planner.memory_model import load_calibration
from ornith_launcher.planner.rank import (
    HEADROOM,
    Candidate,
    Filters,
    Suggestion,
    build_candidates,
    load_tested,
    rank,
    suggest,
)
from ornith_launcher.system.report import SystemReport, check_system
from ornith_launcher.ui.plain import context_label, gib, table

DEFAULT_LIMIT = 40


@dataclass
class Plan:
    """Everything scan works out; the setup wizard reuses it."""

    report: SystemReport
    catalog: Catalog
    entries: list[Entry]
    ranked: list[Candidate]
    suggestions: list[Suggestion]
    families: Families


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--refresh", action="store_true", help="rescan Hugging Face first")
    parser.add_argument("--full", action="store_true", help="list every combination")
    parser.add_argument("--version", dest="versions", action="append", help="e.g. 1.5")
    parser.add_argument("--size", dest="sizes", action="append", help="e.g. 35B")
    parser.add_argument(
        "--source", dest="sources", action="append", choices=("official", "community")
    )
    parser.add_argument("--quant", dest="quants", action="append", help="e.g. Q8_0")
    parser.add_argument("--fits", action="store_true", help="only combinations that fit")
    parser.add_argument("--search", default="", help="words that must appear in the repo or file")
    parser.add_argument(
        "--uncensored", action="store_true", help="also list uncensored (abliterated) models"
    )
    parser.add_argument("--untested-kv", action="store_true", help="also list q8_0 KV cache")
    parser.add_argument("--no-low-bit", action="store_true", help="hide 1- and 2-bit quants")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="rows in --full")


def make_plan(app: App, *, refresh: bool = False, show_uncensored: bool = False) -> Plan:
    families = load_families()
    rules: Rules = load_rules()
    report = check_system(app.host, load_pins(), system=app.os_name, machine=app.machine)
    if refresh:
        catalog = refresh_catalog(app, load_for_refresh(app.paths.catalog_file), families)
    else:
        catalog = load_catalog(app.paths.catalog_file)
    found = entries(catalog, families, rules)
    candidates = build_candidates(found, report.pools, load_calibration(), load_tested())
    ranked = rank(candidates, families)
    suggestions = suggest(ranked, families, show_uncensored=show_uncensored)
    return Plan(report, catalog, found, ranked, suggestions, families)


def refresh_catalog(app: App, previous: Catalog, families: Families) -> Catalog:
    def on_wait(seconds: float, reason: str) -> None:
        app.warn(f"  {reason}: waiting {seconds:.0f} s")

    def progress(state: ScanProgress) -> None:
        count = f" {state.done}/{state.total}" if state.total else ""
        app.warn(f"Scanning Hugging Face{count}: {state.message}")

    hub = HubClient.from_env(app.env, on_wait=on_wait)
    builder = CatalogBuilder(hub, families, progress=progress, clock=app.clock)
    try:
        catalog = builder.build(previous, save=lambda c: save_catalog(c, app.paths.catalog_file))
    except KeyboardInterrupt:
        app.warn("Scan stopped; repos read so far are kept. Run `scan --refresh` to continue.")
        raise
    save_catalog(catalog, app.paths.catalog_file)
    return catalog


def run(app: App, args: argparse.Namespace) -> int:
    plan = make_plan(app, refresh=args.refresh, show_uncensored=args.uncensored)
    print_system(app, plan.report)
    print_catalog_status(app, plan)
    if plan.catalog.source == "empty":
        return 1
    app.say()
    if args.uncensored:
        app.warn(
            "Showing uncensored models: their safety behaviour was removed and they can produce "
            "harmful content. You alone are responsible for how you use them."
        )
    print_suggestions(app, plan.suggestions)
    if args.full:
        filters = Filters(
            versions=frozenset(args.versions or ()),
            sizes=frozenset(s.upper() for s in args.sizes or ()),
            sources=frozenset(args.sources or ()),
            quants=frozenset(q.upper() for q in args.quants or ()),
            show_uncensored=args.uncensored,
            show_untested_kv=args.untested_kv,
            show_low_bit=not args.no_low_bit,
            only_fitting=args.fits,
            search=args.search,
        )
        app.say()
        print_full(app, [c for c in plan.ranked if filters.accepts(c)], args.limit)
    return 0 if not plan.report.blocking else 1


def print_system(app: App, report: SystemReport) -> None:
    p = report.platform
    gpus = ", ".join(
        f"{g.name}" + (" (unified)" if g.unified else f" ({gib(g.memory_total)})")
        if g.memory_total or g.unified
        else g.name
        for g in report.gpus
    )
    docker = report.docker
    docker_text = (
        f"Docker {docker.server_version}"
        if docker.usable
        else ("Docker not usable" if docker.installed else "Docker not installed")
    )
    image = report.flavour.image.flavour if report.flavour else "none"
    app.say(f"System: {p.os} {p.arch}, Python {report.python}")
    app.say(f"GPU:    {gpus or 'none found'}")
    app.say(f"Docker: {docker_text}; llama.cpp image: {image}")
    for pool in report.pools:
        app.say(
            f"Memory: {pool.name}: {gib(pool.total)}, about {gib(pool.baseline)} in use by the "
            f"system, so plans use up to {gib(pool.usable * HEADROOM)} (15% kept free)"
        )
    for problem in report.problems:
        label = "Problem" if problem.severity.value == "blocking" else "Warning"
        app.say(f"{label}: {problem.message}")
        for line in problem.fix.splitlines():
            app.say(f"  Fix: {line}")


def print_catalog_status(app: App, plan: Plan) -> None:
    catalog = plan.catalog
    usable = sum(1 for e in plan.entries if e.usable)
    if catalog.source == "empty":
        app.say("Catalog: empty. Run: ornith-launcher scan --refresh")
        return
    when = datetime.datetime.fromtimestamp(catalog.fetched_at).strftime("%Y-%m-%d %H:%M")
    origin = "shipped list" if catalog.source == "seed" else "last scan"
    app.say(
        f"Catalog: {usable} Ornith model files in {len(catalog.repos)} repos ({origin}, {when})"
    )
    if catalog.is_stale(app.clock()):
        app.say("  It is more than a day old. Update it with: ornith-launcher scan --refresh")


def print_suggestions(app: App, suggestions: list[Suggestion]) -> None:
    if not suggestions:
        app.say("Suggested: nothing fits this machine with 15% memory to spare.")
        app.say("  See every option and its memory need with: ornith-launcher scan --full")
        return
    app.say("Suggested for this machine:")
    rows = []
    for index, s in enumerate(suggestions, 1):
        c = s.candidate
        rows.append(
            [
                str(index),
                _model(c),
                c.entry.classification.quant or "?",
                context_label(c.context),
                c.kv_type,
                gib(c.estimate.total),
                c.entry.repo.id,
            ]
        )
    app.say(table(["#", "Model", "Quant", "Context", "KV", "Memory", "Repo"], rows))
    for index, s in enumerate(suggestions, 1):
        app.say(f"  {index}. {s.reason}")


def print_full(app: App, candidates: list[Candidate], limit: int) -> None:
    app.say(f"All combinations ({len(candidates)}, best first):")
    rows = []
    for c in candidates[: max(limit, 0) or None]:
        rows.append(
            [
                _model(c),
                c.entry.classification.source.value,
                c.entry.classification.quant or "?",
                context_label(c.context),
                c.kv_type,
                "on" if c.vision else ("off" if c.entry.repo.best_mmproj() else "-"),
                c.placement.value,
                gib(c.estimate.total),
                "yes" if c.fits else "no",
                c.entry.repo.licence or "none",
                " ".join(c.badges),
                c.entry.repo.id + "/" + c.entry.file.name,
            ]
        )
    headers = ["Model", "Source", "Quant", "Context", "KV", "Vision", "Placement", "Memory", "Fits"]
    app.say(table(headers + ["Licence", "Badges", "File"], rows))
    if len(candidates) > limit > 0:
        app.say(f"  ... {len(candidates) - limit} more. Show more with --limit, or filter.")


def _model(c: Candidate) -> str:
    cls = c.entry.classification
    if cls.family is not None:
        return cls.family.display_name
    size = cls.shape.label if cls.shape else "?"
    return f"Ornith ? {size}"
