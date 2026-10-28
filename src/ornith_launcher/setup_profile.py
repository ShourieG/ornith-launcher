"""The last setup steps: runtime image, fit check, terminal, chat log, shortcut, profile, launch.

The wizard decides what to run; this module turns that into a saved profile
that ``launch`` can start without asking anything again.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from ornith_launcher import __version__, chat_log
from ornith_launcher.app import App, self_command
from ornith_launcher.installed import record
from ornith_launcher.integrations import opencode
from ornith_launcher.integrations import setup as integrations
from ornith_launcher.integrations.shortcuts import (
    desktop_dir,
    desktop_entry,
    shortcut_name,
    shortcut_path,
    user_dirs_file,
    write_shortcut,
)
from ornith_launcher.integrations.terminals import THIS_TERMINAL, detect_terminals
from ornith_launcher.launch import Launcher, _hop_to_terminal, make_runtime
from ornith_launcher.planner.memory_model import GIB
from ornith_launcher.planner.rank import HEADROOM, Placement
from ornith_launcher.profiles import (
    ModelInfo,
    PlanInfo,
    Profile,
    RuntimeInfo,
    TemplateInfo,
    list_profiles,
    save_profile,
    suggest_name,
    valid_name,
)
from ornith_launcher.runtime.docker import DockerRuntime
from ornith_launcher.scan import Plan
from ornith_launcher.settings import Settings, save_settings
from ornith_launcher.system.memory import PoolKind
from ornith_launcher.templates.compat import read_template, template_sha256
from ornith_launcher.ui.prompts import Cancelled, Choice, PlainPrompter
from ornith_launcher.uninstall import remove_created
from ornith_launcher.wizard import Selection, SetupError

DEFAULT_CACHE_RAM_MIB = 4096


def alias_for(version: str | None, size_class: str | None, quant: str | None) -> str:
    """The model name clients ask for, e.g. ``ornith-1.5-35b-q8_0``."""
    parts = ["ornith", version or "x", (size_class or "model").split("-")[0], quant or ""]
    return "-".join(p for p in parts if p).lower()


def build_profile(
    app: App, selection: Selection, plan: Plan, name: str, settings: Settings
) -> Profile:
    entry = selection.entry
    cls = entry.classification
    candidate = selection.candidate
    report = plan.report
    if report.flavour is None:
        raise SetupError("no llama.cpp image fits this machine", hint="Run: ornith-launcher doctor")
    parts = [str(Path(p).resolve()) for p in selection.model_paths]
    first = Path(parts[0])
    template = read_template(first)
    unified = any(p.kind is PoolKind.UNIFIED for p in report.pools)
    family = cls.family
    model = ModelInfo(
        path=parts[0],
        parts=parts,
        size=sum(Path(p).stat().st_size for p in parts),
        mtime_ns=first.stat().st_mtime_ns,
        location="external" if selection.local is not None else "managed",
        repo=None if entry.repo.id.startswith("local/") else entry.repo.id,
        sha=entry.repo.sha or None,
        file=entry.file.path,
        owner_app=selection.local.file.owner_app if selection.local else None,
        sha256=entry.file.parts[0].sha256 if len(entry.file.parts) == 1 else None,
        mmproj=str(selection.mmproj_path.resolve()) if selection.mmproj_path else None,
        version=cls.version,
        size_class=family.size if family else cls.size_label,
        quant=cls.quant,
        source=_source(selection),
        uncensored=cls.uncensored,
        licence=entry.repo.licence,
    )
    runtime = RuntimeInfo(
        image=report.flavour.image.reference,
        flavour=report.flavour.image.flavour,
        device_args=list(report.flavour.device_args),
        context=candidate.context,
        native_context=candidate.native,
        slots=candidate.slots,
        kv_type=candidate.kv_type,
        port=selection.port,
        alias=alias_for(cls.version, model.size_class, cls.quant),
        yarn_factor=candidate.yarn_factor,
        cache_ram_mib=DEFAULT_CACHE_RAM_MIB,
        placement=candidate.placement.value,
        # Direct I/O avoids a second page-cache copy of the weights on unified memory.
        direct_io=unified and candidate.placement is Placement.SINGLE,
        sampling=dict(family.sampling) if family else {},
        selinux=report.docker.selinux,
        template=TemplateInfo(
            source="gguf", sha256=template_sha256(template) if template else None
        ),
    )
    return Profile(
        name=name,
        created=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        launcher_version=__version__,
        model=model,
        runtime=runtime,
        plan=PlanInfo(
            estimate_gib=round(candidate.estimate.total / GIB, 1), level=candidate.level.value
        ),
        consents={"disclaimer_version": settings.disclaimer_version, **selection.consents},
    )


def _source(selection: Selection) -> str:
    """official / community, or local-unknown for a file on disk with no catalog match."""
    if selection.local is not None and selection.local.likely is None:
        return "local-unknown"
    return selection.entry.classification.source.value


def ensure_image(app: App, runtime: DockerRuntime, reference: str) -> None:
    if runtime.image_present(reference):
        return
    app.say()
    app.say(f"Downloading the llama.cpp image {reference.split('@')[0]}...")
    runtime.pull(reference)
    record(app.paths, "images", reference)  # only an image this launcher pulled is ever removed


def fit_check(app: App, runtime: DockerRuntime, profile: Profile, plan: Plan) -> None:
    """Ask llama.cpp itself how much memory the model needs, and compare with the budget."""
    app.say()
    app.say("Checking the plan with llama.cpp's own memory estimate...")
    result = runtime.fit_check(profile)
    if result is None:
        app.say("  The check did not run; the launcher's own estimate is used.")
        return
    profile.plan.fitcheck = {name: values for name, values in result.devices.items()}
    budget = sum(int(p.usable * HEADROOM) for p in plan.report.pools)
    needed = result.total_mib * 1024**2
    app.say(f"  llama.cpp expects {needed / GIB:.1f} GiB before runtime overhead.")
    if needed > budget:
        app.say(
            "  Warning: that is more than this machine can spare. Pick a smaller context or "
            "quant if loading fails."
        )


def choose_terminal(app: App, prompter: PlainPrompter, plan: Plan) -> dict[str, object]:
    if plan.report.platform.headless:
        return {"id": THIS_TERMINAL}
    terminals = detect_terminals(app.host)
    options = []
    if terminals.default is not None:
        options.append(terminals.default)
    options += [t for t in terminals.installed if t not in options]
    choices = [
        Choice((t.name, "system default" if t is terminals.default else "")) for t in options
    ]
    choices.append(Choice(("This terminal", "")))
    picked = prompter.choose(
        "Terminal for the server monitor:", ["Terminal", ""], choices, default=0
    )
    if int(picked) == len(options):
        return {"id": THIS_TERMINAL}
    terminal = options[int(picked)]
    return {"id": terminal.id, "argv": list(terminal.argv)}


def offer_chat_log(app: App, prompter: PlainPrompter, plan: Plan, profile: Profile) -> None:
    """A second window with requests and replies at every launch (default no); saved."""
    if plan.report.platform.headless or chat_log.log_terminal(app, profile) is None:
        return
    app.say()
    prompter.paragraph(
        "A chat log window shows each request sent to this model and its reply as it "
        "streams: what you or your coding tool sent, the model's thinking, its answer and "
        "its tool calls. Nothing is saved to disk. The server's own log stays in the server "
        "window. Ctrl+C or closing the chat log closes only that window, and it closes by "
        "itself when the server stops."
    )
    if prompter.confirm("Also open a chat log window when this model starts?", default=False):
        profile.terminal["chat_log"] = True


def offer_shortcut(app: App, prompter: PlainPrompter, plan: Plan, profile: Profile) -> None:
    """A launch icon on the desktop (default yes), then the applications menu (default no)."""
    if not plan.report.platform.headless:
        ask_shortcuts(app, prompter, profile)


def ask_shortcuts(app: App, prompter: PlainPrompter, profile: Profile) -> None:
    """The two questions, skipping whatever the profile already has."""
    comment = f"Start Ornith ({profile.name}) with ornith-launcher (unofficial)"
    in_terminal = profile.terminal.get("id") == THIS_TERMINAL
    entry = desktop_entry(
        profile.name, self_command(), comment, in_terminal, chat_action=chat_log.enabled(profile)
    )
    desktop = desktop_dir(app.host.read_text(str(user_dirs_file(app.env, app.home))), app.home)
    # A profile set up earlier may have one already; only the missing one is asked about.
    on_desktop = _has_file(profile, "desktop_copy")
    if not on_desktop and desktop is not None and desktop.is_dir():
        if prompter.confirm("Put a launch icon for this model on your desktop?", default=True):
            path = desktop / shortcut_name(profile.name)
            write_shortcut(path, entry)
            record(app.paths, "shortcuts", str(path))
            profile.integrations["desktop_copy"] = str(path)
            on_desktop = True
            app.say(f"  Created {path}.")
            if not _trust_launcher(app, path):
                app.say(
                    '  If the icon says "Allow Launching", right-click it and choose that once.'
                )
    if _has_file(profile, "shortcut"):
        return
    question = (
        "Also add it to your applications menu?"
        if on_desktop
        else "Add a launcher for this model to your applications menu?"
    )
    if prompter.confirm(question, default=False):
        path = shortcut_path(profile.name, app.env, app.home)
        write_shortcut(path, entry)
        record(app.paths, "shortcuts", str(path))
        profile.integrations["shortcut"] = str(path)
        app.say(f"  Created {path}. It appears in your applications menu.")


def _has_file(profile: Profile, key: str) -> bool:
    path = profile.integrations.get(key)
    return bool(path) and Path(path).is_file()


def _trust_launcher(app: App, path: Path) -> bool:
    """Mark our own desktop file as trusted, so GNOME launches it without asking first."""
    if app.host.which("gio") is None:
        return False
    result = app.host.run(["gio", "set", str(path), "metadata::trusted", "true"])
    return result is not None and result.ok


def finish_setup(
    app: App,
    prompter: PlainPrompter,
    selection: Selection,
    plan: Plan,
    settings: Settings,
    name: str | None = None,
) -> Profile:
    runtime = make_runtime(app)
    existing = list_profiles(app.paths)
    cls = selection.entry.classification
    name = name or suggest_name(
        cls.version,
        cls.family.size if cls.family else cls.size_label,
        cls.quant,
        selection.candidate.context,
        existing,
    )
    if not valid_name(name):
        raise SetupError(f"{name!r} is not a valid profile name", hint="Use e.g. --name ornith-35b")
    profile = build_profile(app, selection, plan, name, settings)
    ensure_image(app, runtime, profile.runtime.image)
    fit_check(app, runtime, profile, plan)
    is_default = settings.default_profile is None or settings.default_profile not in existing
    choice = integrations.choose(app, prompter)
    if choice != integrations.NONE:
        app.say()  # the results of the choice, set off from the list
    try:
        integrations.configure(app, prompter, profile, choice, is_default)
        profile.terminal = choose_terminal(app, prompter, plan)
        offer_chat_log(app, prompter, plan, profile)
        offer_shortcut(app, prompter, plan, profile)
    except (Cancelled, KeyboardInterrupt):
        # No profile is saved, so nothing would ever clean these up later.
        record = profile.integrations.pop("opencode", None)
        if record:
            opencode.restore(record)
            app.say(f"Put {record['config']} back as it was.")
        remove_created(app, profile)
        raise
    save_profile(app.paths, profile)
    if is_default:
        settings.default_profile = profile.name
        save_settings(app.paths, settings)
    app.say()
    app.say(prompter.bold(f"Saved the profile {profile.name}."))
    app.say(f"Start it any time with: ornith-launcher launch {profile.name}")
    return profile


def launch_now(app: App, prompter: PlainPrompter, profile: Profile) -> int:
    if not prompter.confirm("Launch now?", default=False):
        return 0
    if _hop_to_terminal(app, profile):
        return 0
    app.say()
    return Launcher(app, make_runtime(app), prompter).serve(profile)


def repair(app: App, prompter: PlainPrompter, name: str) -> int:
    """Put back what a profile needs: the image, a missing download, a changed external file."""
    from ornith_launcher.catalog.hfapi import HubClient
    from ornith_launcher.download import Downloader, HttpRangeSource, Target
    from ornith_launcher.profiles import load_profile

    profile = load_profile(app.paths, name)
    runtime = make_runtime(app)
    ensure_image(app, runtime, profile.runtime.image)
    model = profile.model
    missing = [p for p in model.parts if not Path(p).is_file()]
    if missing and model.location == "external":
        raise SetupError(
            f"the model file {missing[0]} is gone; it was not downloaded by ornith-launcher",
            hint="Run ornith-launcher setup to pick a model again.",
        )
    if missing:
        if not (model.repo and model.sha):
            raise SetupError("the profile does not record where the model came from")
        hub = HubClient.from_env(app.env)
        downloader = Downloader(source=HttpRangeSource(token=app.env.get("HF_TOKEN") or None))
        folder = Path(model.parts[0]).parent
        repo_dir = str(Path(model.file).parent) if model.file and "/" in model.file else ""
        for part in missing:
            path_in_repo = f"{repo_dir}/{Path(part).name}" if repo_dir else Path(part).name
            app.say(f"Downloading {Path(part).name} again...")
            size = model.size if len(model.parts) == 1 else 0
            if not size:
                raise SetupError(
                    "a part of a split model is missing", hint="Run ornith-launcher setup again."
                )
            target = Target(
                hub.resolve_url(model.repo, model.sha, path_in_repo),
                folder / Path(part).name,
                size,
                model.sha256,
            )
            downloader.fetch(target)
    elif model.location == "external":
        first = Path(model.parts[0])
        size = sum(Path(p).stat().st_size for p in model.parts)
        if size != model.size or first.stat().st_mtime_ns != model.mtime_ns:
            app.say(f"{first} changed since setup (it may have been updated by its app).")
            if not prompter.confirm("Use it as it is now?", default=True):
                return 1
            model.size = size
            model.mtime_ns = first.stat().st_mtime_ns
    save_profile(app.paths, profile)
    app.say(f"{name} is repaired. Start it with: ornith-launcher launch {name}")
    return 0


def change_integrations(app: App, prompter: PlainPrompter, name: str) -> int:
    """``setup --integrations NAME``: connect a profile to other tools, or disconnect it."""
    from ornith_launcher.profiles import load_profile
    from ornith_launcher.settings import load_settings

    profile = load_profile(app.paths, name)
    before = profile.runtime.template.source
    is_default = load_settings(app.paths).default_profile == name
    integrations.configure(app, prompter, profile, integrations.choose(app, prompter), is_default)
    save_profile(app.paths, profile)
    if profile.runtime.template.source != before and make_runtime(app).status(profile).alive:
        app.say("The chat template changed; restart the server for it to take effect:")
        app.say(f"  ornith-launcher stop {name} && ornith-launcher launch {name}")
    return 0


def add_shortcuts(app: App, prompter: PlainPrompter, name: str) -> int:
    """``setup --shortcuts NAME``: the desktop icon and menu questions for a saved profile."""
    from ornith_launcher.profiles import load_profile
    from ornith_launcher.system.platform import detect_platform

    profile = load_profile(app.paths, name)
    if detect_platform(app.host).headless:
        raise SetupError(
            "there is no desktop session here, so there is nowhere to put a launch icon",
            hint=f"Run this from a desktop terminal, or start the model with: "
            f"ornith-launcher launch {name}",
        )
    if _has_file(profile, "desktop_copy") and _has_file(profile, "shortcut"):
        app.say(f"{name} already has a desktop icon and an applications menu entry.")
        return 0
    ask_shortcuts(app, prompter, profile)
    save_profile(app.paths, profile)
    return 0
