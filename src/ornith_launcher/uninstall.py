"""Undoing what the launcher created: one profile (``remove``) or everything (``uninstall``).

Every file the launcher writes outside its own folders is recorded in the
profile, so removal is exact. Model files found on the machine (external) are
never deleted or offered for deletion; downloaded ones are deleted only after
the user agrees.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ornith_launcher.app import App
from ornith_launcher.profiles import Profile, list_profiles, load_profile
from ornith_launcher.runtime.docker import DockerRuntime
from ornith_launcher.settings import load_settings
from ornith_launcher.ui.prompts import PlainPrompter

GIB = 1024**3


def created_files(profile: Profile) -> list[Path]:
    """Files outside our own folders that setup created for this profile."""
    paths = []
    integrations = profile.integrations
    for key in ("shortcut", "desktop_copy"):
        if integrations.get(key):
            paths.append(Path(integrations[key]))
    return paths


def other_profiles_using(app: App, profile: Profile, path: str) -> list[str]:
    users = []
    for name in list_profiles(app.paths):
        if name == profile.name:
            continue
        other = load_profile(app.paths, name)
        if path in other.model.parts or path == other.model.mmproj:
            users.append(name)
    return users


def remove_profile(
    app: App, profile: Profile, runtime: DockerRuntime, prompter: PlainPrompter
) -> None:
    runtime.stop(profile)
    if profile.integrations.get("claude_code"):
        from ornith_launcher.integrations.setup import remove_claude_files

        remove_claude_files(profile.integrations["claude_code"])
        app.say("Deleted the Claude Code wrapper.")
    for path in created_files(profile):
        if path.exists() or path.is_symlink():
            path.unlink()
            app.say(f"Deleted {path}")
    if profile.integrations.get("opencode"):
        from ornith_launcher.integrations.opencode import remove_entry

        remove_entry(app, profile)
    template = profile.runtime.template
    if template.source == "patched" and template.file:
        Path(template.file).unlink(missing_ok=True)
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
        for path in files:
            Path(path).unlink()
        folder = Path(files[0]).parent
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
        app.say("Deleted.")


def run_uninstall(app: App, args) -> int:
    prompter = PlainPrompter(read_line=app.read_line, out=app.out, assume_yes=args.yes)
    runtime = DockerRuntime(app.host)
    names = list_profiles(app.paths)
    paths = app.paths
    ours = [p for p in (paths.config, paths.data, paths.cache, paths.state) if p.exists()]
    app.say("This removes everything ornith-launcher created:")
    for name in names:
        app.say(f"  profile {name} (its server, shortcuts and tool settings)")
    for folder in ours:
        app.say(f"  {folder}")
    models = load_settings(paths).models_path(paths)
    app.say(f"Downloaded models in {models} are asked about separately.")
    app.say("Model files found elsewhere on this machine are never touched.")
    if not prompter.confirm("Continue?", default=False):
        return 1
    for name in names:
        remove_profile(app, load_profile(paths, name), runtime, prompter)
    for folder in ours:
        shutil.rmtree(folder, ignore_errors=True)
        app.say(f"Deleted {folder}")
    app.say("ornith-launcher's files are gone. Delete this folder to remove the tool itself.")
    return 0
