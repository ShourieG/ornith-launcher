"""The ``doctor`` command: a read-only check of everything the launcher depends on.

It changes nothing. Each problem comes with the exact fix, so the output can be
pasted into an issue or followed step by step.
"""

from __future__ import annotations

import argparse
import datetime

from ornith_launcher.app import App
from ornith_launcher.catalog.catalog import load_catalog
from ornith_launcher.integrations import claude_code
from ornith_launcher.integrations.installers import CLAUDE_CODE, OPENCODE, find
from ornith_launcher.launch import make_runtime, preflight
from ornith_launcher.pins import load_pins
from ornith_launcher.profiles import list_profiles, load_profile
from ornith_launcher.runtime.native import QUARANTINE, runtime_root
from ornith_launcher.scan import print_system
from ornith_launcher.settings import load_settings
from ornith_launcher.system.report import check_system

GIB = 1024**3


def run_doctor(app: App, args: argparse.Namespace) -> int:
    problems = 0
    report = check_system(
        app.host,
        load_pins(),
        system=app.os_name,
        machine=app.machine,
        runtime_root=runtime_root(app.paths),
    )
    print_system(app, report)
    problems += len(report.blocking)

    settings = load_settings(app.paths)
    models = settings.models_path(app.paths)
    existing = next((p for p in (models, *models.parents) if p.exists()), None)
    free = app.host.disk_free(str(existing)) if existing else None
    free_text = f"{free / GIB:.0f} GiB free" if free is not None else "free space unknown"
    app.say(f"Models folder: {models} ({free_text})")

    runtime = make_runtime(app)
    if report.flavour is not None and report.docker.usable:
        image = report.flavour.image.reference
        state = "present" if runtime.image_present(image) else "not downloaded yet"
        app.say(f"llama.cpp image {report.flavour.image.tag}: {state}")
    if report.native is not None:
        reference = report.native.reference
        state = "present" if runtime.image_present(reference) else "not downloaded yet"
        app.say(f"{runtime.describe(reference)}: {state}")
        if runtime.quarantined(reference):
            folder = runtime.folder(reference)
            app.say(
                "  macOS marked these files as downloaded from the internet, so it may refuse "
                "to run them."
            )
            app.say(f"  Fix (their sha256 was checked): xattr -dr {QUARANTINE} {folder}")
            problems += 1
    check_image = report.docker.usable or report.native is not None

    catalog = load_catalog(app.paths.catalog_file)
    if catalog.source == "empty":
        app.say("Catalog: empty. Fix: ornith-launcher scan --refresh")
        problems += 1
    else:
        when = datetime.datetime.fromtimestamp(catalog.fetched_at).strftime("%Y-%m-%d")
        app.say(f"Catalog: {len(catalog.repos)} repos from {when} ({catalog.source})")

    for name in list_profiles(app.paths):
        profile = load_profile(app.paths, name)
        found = preflight(app, profile, runtime, check_image=check_image)
        state = "ok" if not found else f"{len(found)} problem(s)"
        app.say(f"Profile {name}: {state}")
        for problem in found:
            app.say(f"  {problem}")
        problems += len(found)
        wrapper = (profile.integrations.get("claude_code") or {}).get("wrapper")
        if wrapper and not claude_code.on_path(claude_code.wrapper_dir(app.home), app.env):
            app.say(
                f"  {claude_code.wrapper_dir(app.home)} is not on PATH; run {wrapper} by its path."
            )

    for installer in (CLAUDE_CODE, OPENCODE):
        path = find(installer, app.host, app.home)
        app.say(f"{installer.name}: {path or 'not installed (optional)'}")

    app.say()
    app.say(
        "No problems found."
        if not problems
        else f"{problems} problem(s) found; see the fixes above."
    )
    return 0 if not problems else 1
