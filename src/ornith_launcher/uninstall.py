"""Undoing what the launcher created: one profile (``remove``), or the uninstall menu.

The menu lists only what applies: one setup or all setups once a setup exists,
unfinished downloads while a stopped download is left (``unfinished.py``), and
everything.

Only what ornith-launcher itself made is ever removed, and that is read from its
own files: the profiles, and the record in ``installed.py`` (downloads, desktop
icons, applications-menu entries, pulled images). Model files found on the
machine or entered by the user are never deleted or offered for deletion.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ornith_launcher import chat_log
from ornith_launcher.app import App
from ornith_launcher.installed import forget, load_installed
from ornith_launcher.profiles import Profile, delete_profile, list_profiles, load_profile
from ornith_launcher.runtime.docker import DockerRuntime, digest_reference
from ornith_launcher.settings import load_settings, save_settings
from ornith_launcher.ui.prompts import BACK, Cancelled, Choice, PlainPrompter
from ornith_launcher.unfinished import Unfinished, forget_choices, unfinished_downloads

GIB = 1024**3
PARTIAL_SUFFIXES = ("", ".part", ".part.json")  # a download and its resume files

# What the uninstall menu did, for the caller.
NOTHING, REMOVED, EVERYTHING = "nothing", "removed", "everything"


def created_files(profile: Profile) -> list[Path]:
    """Files outside our own folders that setup created for this profile."""
    paths = []
    integrations = profile.integrations
    for key in ("shortcut", "desktop_copy"):
        if integrations.get(key):
            paths.append(Path(integrations[key]))
    return paths


def tool_entries(profile: Profile) -> list[str]:
    """The coding-tool settings setup made for this profile, as uninstall lists them."""
    entries = []
    opencode = profile.integrations.get("opencode") or {}
    if opencode.get("provider") and Path(opencode.get("config", "")).is_file():
        entries.append(
            f"OpenCode: the model {profile.runtime.alias} (provider {opencode['provider']}) in "
            f"{opencode['config']}; a backup of the file is kept"
        )
    claude = profile.integrations.get("claude_code") or {}
    for key in ("wrapper", "alias"):
        if claude.get(key) and (Path(claude[key]).exists() or Path(claude[key]).is_symlink()):
            entries.append(f"Claude Code: {claude[key]}")
    return entries


def other_profiles_using(app: App, profile: Profile, path: str) -> list[str]:
    users = []
    for name in list_profiles(app.paths):
        if name == profile.name:
            continue
        other = load_profile(app.paths, name)
        if path in other.model.parts or path == other.model.mmproj:
            users.append(name)
    return users


def remove_created(app: App, profile: Profile) -> None:
    """Undo the integrations and shortcut setup made for this profile; model files stay."""
    if profile.integrations.get("claude_code"):
        from ornith_launcher.integrations.setup import remove_claude_files

        remove_claude_files(profile.integrations["claude_code"])
        app.say("  Deleted the Claude Code wrapper.")
    for path in created_files(profile):
        if path.exists() or path.is_symlink():
            path.unlink()
            app.say(f"  Deleted {path}")
        forget(app.paths, "shortcuts", str(path))
    if profile.integrations.get("opencode"):
        from ornith_launcher.integrations.opencode import remove_entry

        remove_entry(app, profile)
    template = profile.runtime.template
    if template.source == "patched" and template.file:
        Path(template.file).unlink(missing_ok=True)


def remove_profile(
    app: App, profile: Profile, runtime: DockerRuntime, prompter: PlainPrompter
) -> None:
    """``remove NAME``: everything the profile made, then a question about its download."""
    runtime.stop(profile)
    chat_log.shutdown(app, profile.name)
    remove_created(app, profile)
    if profile.model.location != "managed":
        app.say(f"The model file stays where it is: {profile.model.parts[0]}")
        return
    files = [p for p in [*profile.model.parts, profile.model.mmproj] if p]
    files = [p for p in files if Path(p).exists() and not other_profiles_using(app, profile, p)]
    if not files:
        return
    size = sum(Path(p).stat().st_size for p in files)
    app.say(f"Downloaded model files ({size / GIB:.1f} GiB):")
    for path in files:
        app.say(f"  {path}")
    if prompter.confirm("Delete them too?", default=False):
        delete_downloads(app, [Path(p) for p in files])
        app.say("Deleted.")


# The uninstall menu ------------------------------------------------------------------------


@dataclass
class Removal:
    """What a removal will delete, worked out before asking; nothing is touched yet."""

    profiles: list[Profile] = field(default_factory=list)
    tool_entries: list[str] = field(default_factory=list)  # OpenCode entries, Claude Code wrappers
    model_files: list[Path] = field(default_factory=list)  # downloads recorded as ours
    shortcuts: list[Path] = field(default_factory=list)  # icons and menu entries recorded as ours
    kept_models: list[Path] = field(default_factory=list)  # not downloaded by us: never deleted
    images: list[str] = field(default_factory=list)  # Docker references we pulled
    folders: list[Path] = field(default_factory=list)  # our own folders (everything only)

    @property
    def empty(self) -> bool:
        return not (
            self.profiles or self.model_files or self.shortcuts or self.images or self.folders
        )

    @property
    def size(self) -> int:
        return sum(p.stat().st_size for p in self.model_files if p.exists())


def plan_removal(app: App, profiles: list[Profile], everything: bool = False) -> Removal:
    """Only files the profiles or the install record say the launcher made are listed."""
    installed = load_installed(app.paths)
    removing = {p.name for p in profiles}
    kept_profiles = [load_profile(app.paths, n) for n in list_profiles(app.paths)]
    kept_profiles = [p for p in kept_profiles if p.name not in removing]
    in_use = {path for p in kept_profiles for path in _model_files(p)}
    ours = set(installed.downloads)
    for profile in [*profiles, *kept_profiles]:
        if profile.model.location == "managed":
            ours.update(_model_files(profile))

    removal = Removal(profiles=list(profiles))
    removal.tool_entries = [entry for profile in profiles for entry in tool_entries(profile)]
    candidates = [path for profile in profiles for path in _model_files(profile)]
    if everything:
        candidates += installed.downloads
    for path in dict.fromkeys(candidates):  # keeps order, drops repeats
        if path not in ours:
            if Path(path).exists():
                removal.kept_models.append(Path(path))
            continue
        if path in in_use:
            continue
        for suffix in PARTIAL_SUFFIXES:
            file = Path(path + suffix)
            if file.exists():
                removal.model_files.append(file)
    shortcuts = [str(s) for profile in profiles for s in created_files(profile)]
    if everything:
        shortcuts += installed.shortcuts
    removal.shortcuts = [Path(s) for s in dict.fromkeys(shortcuts) if Path(s).exists()]
    if everything:
        removal.images = list(installed.images)
        paths = app.paths
        removal.folders = [
            p for p in (paths.config, paths.data, paths.cache, paths.state) if p.exists()
        ]
    return removal


def uninstall_menu(app: App, prompter: PlainPrompter) -> str:
    """The uninstall choices; returns NOTHING, REMOVED or EVERYTHING."""
    runtime = DockerRuntime(app.host)
    while True:
        names = list_profiles(app.paths)
        unfinished = unfinished_downloads(app.paths)
        # Only what applies is listed: a greyed-out row would look like a dead button.
        options = []
        if names:
            options += [
                (
                    "one",
                    ("Remove one setup", "choose its setup, launch icons, model or model server"),
                ),
                ("all", ("Remove all setups", "the same for every setup")),
            ]
        if unfinished:
            stopped = sum(u.done for u in unfinished) / GIB
            options.append(
                (
                    "unfinished",
                    (
                        "Remove unfinished downloads",
                        f"downloads that were stopped ({len(unfinished)}, {stopped:.1f} GiB "
                        "so far); finished models are kept",
                    ),
                )
            )
        options.append(
            (
                "everything",
                (
                    "Remove everything",
                    "all setups, every model it downloaded, the llama.cpp image it pulled "
                    "and its own settings",
                ),
            )
        )
        picked = prompter.choose(
            "Uninstall",
            ["Option", "Removes"],
            [Choice(cells) for _, cells in options],
            notice=(
                "Only what ornith-launcher downloaded or created is removed. Models found "
                "elsewhere on this machine are never touched."
            ),
            back=True,
        )
        if picked == BACK:
            return NOTHING
        action = options[int(picked)][0]
        everything = action == "everything"
        if action == "unfinished":
            removal = _choose_unfinished(prompter, unfinished)
            if removal is None:
                continue
        elif action == "one":
            profile = _choose_profile(app, prompter, names)
            if profile is None:
                continue
            parts = _choose_parts(app, prompter, profile, runtime)
            if parts is None:
                continue
            setup, server = parts
            removal = plan_removal(app, [profile]) if setup else Removal()
            if server:
                removal.images = [profile.runtime.image]
        else:
            profiles = [load_profile(app.paths, name) for name in names]
            removal = plan_removal(app, profiles, everything=everything)
        if not _confirm(app, prompter, removal):
            continue
        apply_removal(app, removal, runtime)
        return EVERYTHING if everything else REMOVED


def apply_removal(app: App, removal: Removal, runtime: DockerRuntime) -> None:
    settings = load_settings(app.paths)
    app.say()
    for profile in removal.profiles:
        runtime.stop(profile)
        chat_log.shutdown(app, profile.name)
        remove_created(app, profile)
        delete_profile(app.paths, profile.name)
        if settings.default_profile == profile.name:
            settings.default_profile = None
            save_settings(app.paths, settings)
        app.say(f"  Removed the setup {profile.name}.")
    for path in removal.shortcuts:
        if path.exists():
            path.unlink()
            app.say(f"  Deleted {path}")
        forget(app.paths, "shortcuts", str(path))
    if removal.model_files:
        delete_downloads(app, removal.model_files)
        app.say(f"  Deleted {len(removal.model_files)} downloaded model file(s).")
    for reference in removal.images:
        # A running server of ours would make Docker refuse to remove its image.
        for name in list_profiles(app.paths):
            other = load_profile(app.paths, name)
            if digest_reference(other.runtime.image) == digest_reference(reference):
                runtime.stop(other)
                chat_log.shutdown(app, other.name)
        outcome, reason = runtime.remove_image(reference)
        if outcome == "failed":
            app.say(f"  Docker did not remove the llama.cpp image ({reason}).")
            app.say(f"    Remove it later with: docker image rm {reference.split('@')[0]}")
            continue
        forget(app.paths, "images", reference)
        if outcome == "kept":
            app.say(
                "  Removed the launcher's reference to the llama.cpp image; other tags keep it."
            )
        else:
            app.say("  Deleted the llama.cpp image.")
    for folder in removal.folders:
        shutil.rmtree(folder, ignore_errors=True)
        app.say(f"  Deleted {folder}")
    if removal.folders:
        app.say()
        app.say("ornith-launcher's files are gone. Delete this folder to remove the tool itself.")


def run_uninstall(app: App, args) -> int:
    from ornith_launcher.wizard import make_prompter

    prompter = make_prompter(app)
    try:
        uninstall_menu(app, prompter)
    except Cancelled:
        prompter.quit_screen(["Nothing more was removed."])
        return 1
    return 0


def has_installs(app: App) -> bool:
    """True when the launcher's own files say it created something to remove."""
    return bool(list_profiles(app.paths)) or not load_installed(app.paths).empty


def _model_files(profile: Profile) -> list[str]:
    return [p for p in [*profile.model.parts, profile.model.mmproj] if p]


def _choose_profile(app: App, prompter: PlainPrompter, names: list[str]) -> Profile | None:
    profiles = [load_profile(app.paths, name) for name in names]
    picked = prompter.choose(
        "Which setup?",
        ["Setup", "Model", "Desktop icon", "Menu entry", "Model file"],
        [
            Choice(
                (
                    p.name,
                    f"{p.model.version or '?'} {p.model.size_class or ''} {p.model.quant or ''}",
                    "yes" if p.integrations.get("desktop_copy") else "-",
                    "yes" if p.integrations.get("shortcut") else "-",
                    "downloaded" if p.model.location == "managed" else "found elsewhere (kept)",
                )
            )
            for p in profiles
        ],
        back=True,
    )
    return None if picked == BACK else profiles[int(picked)]


def server_kept_reason(app: App, profile: Profile, runtime: DockerRuntime) -> str:
    """Why the model server cannot go with this setup, or "" when it can.

    Only an image this launcher pulled is offered, and only when no other setup needs it.
    """
    reference = profile.runtime.image
    if reference not in load_installed(app.paths).images:
        return (
            "The model server (llama.cpp image) was already on this machine before "
            "ornith-launcher pulled it, so it is kept."
        )
    others = [
        name
        for name in list_profiles(app.paths)
        if name != profile.name
        and digest_reference(load_profile(app.paths, name).runtime.image)
        == digest_reference(reference)
    ]
    if others:
        users = " and ".join(others) if len(others) < 3 else f"{len(others)} other setups"
        verb = "needs" if len(others) == 1 else "need"
        return f"The model server is not offered: {users} also {verb} it."
    if not runtime.image_present(reference):
        return "The model server is no longer on this machine."
    return ""


def _choose_parts(
    app: App, prompter: PlainPrompter, profile: Profile, runtime: DockerRuntime
) -> tuple[bool, bool] | None:
    """(remove the setup, remove the model server) for one setup, or None for Back."""
    reason = server_kept_reason(app, profile, runtime)
    things = ["setup"]
    if profile.integrations.get("desktop_copy"):
        things.append("launch icon")
    if profile.integrations.get("shortcut"):
        things.append("menu entry")
    if (profile.integrations.get("opencode") or {}).get("provider"):
        things.append("OpenCode entry")
    if profile.integrations.get("claude_code"):
        things.append("Claude Code wrapper")
    managed = profile.model.location == "managed"
    if managed:
        things.append("the model it downloaded")
    label = _joined(things)
    picked = prompter.choose(
        f"Remove {profile.name}:",
        ["Part", "Note"],
        [
            Choice(
                (
                    label[0].upper() + label[1:],
                    "" if managed else "the model file stays where it is",
                )
            ),
            Choice(("The model server", "the llama.cpp image"), enabled=not reason),
            Choice(("Both", ""), enabled=not reason),
        ],
        notice=reason,
        back=True,
    )
    if picked == BACK:
        return None
    return picked in (0, 2), picked in (1, 2)


def _joined(words: list[str]) -> str:
    """["setup", "launch icon", "menu entry"] -> "setup, launch icon and menu entry"."""
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _confirm(app: App, prompter: PlainPrompter, removal: Removal) -> bool:
    prompter.new_screen("Uninstall: what will be deleted")
    if removal.empty:
        app.say("Nothing that ornith-launcher created is left to remove.")
        return False
    groups = [
        ("Setups", [f"{p.name}, with its server settings" for p in removal.profiles]),
        ("Coding-tool settings", removal.tool_entries),
        ("Launch icons and menu entries", [str(p) for p in removal.shortcuts]),
        (
            f"Downloaded model files ({removal.size / GIB:.1f} GiB)",
            [str(p) for p in removal.model_files],
        ),
        ("The model server (llama.cpp image)", [r.split("@")[0] for r in removal.images]),
        ("ornith-launcher's own folders", [str(p) for p in removal.folders]),
        ("Kept (not downloaded by ornith-launcher)", [str(p) for p in removal.kept_models]),
    ]
    first = True
    for label, items in groups:
        if not items:
            continue
        if first:
            app.say(prompter.bold(label))
            first = False
        else:
            prompter.section(label)
        for item in items:
            app.say(f"  {item}")
    return prompter.confirm("Delete these?", default=False)


def _choose_unfinished(prompter: PlainPrompter, unfinished: list[Unfinished]) -> Removal | None:
    """Pick one stopped download, or all of them; None to go back."""
    if len(unfinished) == 1:
        return Removal(model_files=unfinished[0].files())
    choices = [
        Choice((u.repo_id, u.model_name, f"{u.done / GIB:.1f} of {u.total / GIB:.1f} GiB"))
        for u in unfinished
    ]
    choices.append(Choice(("All of them", "", "")))
    picked = prompter.choose(
        "Which unfinished download?", ["Repository", "File", "Downloaded"], choices, back=True
    )
    if picked == BACK:
        return None
    chosen = unfinished if int(picked) == len(unfinished) else [unfinished[int(picked)]]
    return Removal(model_files=[f for u in chosen for f in u.files()])


def delete_downloads(app: App, files: list[Path]) -> None:
    """Delete downloaded files and their folders once empty, up to the models folder."""
    root = load_settings(app.paths).models_path(app.paths)
    for file in files:
        file.unlink(missing_ok=True)
        name = str(file)
        for suffix in PARTIAL_SUFFIXES[1:]:
            name = name.removesuffix(suffix)
        forget(app.paths, "downloads", name)
        recorded = load_installed(app.paths).downloads
        if not any(Path(path).parent == file.parent for path in recorded):
            forget_choices(app.paths, file.parent)  # the settings saved for resuming it
        folder = file.parent
        while folder != root and root in folder.parents and folder.is_dir():
            if any(folder.iterdir()):
                break
            folder.rmdir()
            folder = folder.parent
