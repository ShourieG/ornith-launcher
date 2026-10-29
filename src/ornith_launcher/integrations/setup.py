"""Setup screen 5: connect the model to Claude Code and/or OpenCode.

Claude Code needs the chat-template fix (``templates.compat``); it is applied
only when Claude Code is chosen (D15), to a copy in the tool's data folder.
OpenCode alone keeps the model's own template.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from ornith_launcher import installed
from ornith_launcher.app import App
from ornith_launcher.config import atomic_write_text
from ornith_launcher.integrations import claude_code, opencode
from ornith_launcher.integrations.installers import (
    CLAUDE_CODE,
    OPENCODE,
    Installer,
    find,
    install,
    new_window_finds,
    path_line,
)
from ornith_launcher.profiles import Profile, TemplateInfo
from ornith_launcher.templates import compat
from ornith_launcher.ui.prompts import Choice, PlainPrompter

NONE, OPENCODE_ONLY, CLAUDE_ONLY, BOTH = "none", "opencode", "claude", "both"
CHOICES = (
    (NONE, "None", "just the model server"),
    (OPENCODE_ONLY, "OpenCode", "the model's chat template is used as it is"),
    (CLAUDE_ONLY, "Claude Code", "suggested; applies a small chat-template fix"),
    (BOTH, "OpenCode + Claude Code", "applies a small chat-template fix"),
)
SUGGESTED = 2  # Claude Code, the tool Ornith's makers suggest


def choose(app: App, prompter: PlainPrompter) -> str:
    claude = find(CLAUDE_CODE, app.host, app.home)
    code = find(OPENCODE, app.host, app.home)
    prompter.new_screen("Coding tools")
    bold = prompter.bold
    # One line per tool, then each note apart, so the screen reads in steps.
    app.say(f"  {bold('Claude Code')}  {'installed' if claude else 'not installed'}")
    app.say(f"  {bold('OpenCode')}     {'installed' if code else 'not installed'}")
    app.say()
    prompter.paragraph(
        f"{bold('Claude Code')} needs a small chat-template fix for this model; the fix is "
        "applied to a copy, the model file is not changed.",
        indent="  ",
    )
    app.say()
    prompter.paragraph(
        f"{bold('OpenCode')} also gets picture instructions: a short file, added to OpenCode's "
        "instructions, that goes into the system prompt of every OpenCode session, for every "
        "model, and tells models how to show charts and diagrams in the chat. Uninstall removes "
        "it with the last Ornith model.",
        indent="  ",
    )
    app.say()
    prompter.paragraph(f"Note: {claude_code.ANTHROPIC_NOTICE}", indent="  ")
    picked = prompter.choose(
        "Connect the model to a coding tool?",
        ["Option", ""],
        [Choice((label, note)) for _, label, note in CHOICES],
        default=SUGGESTED,
    )
    return CHOICES[int(picked)][0]


def configure(
    app: App, prompter: PlainPrompter, profile: Profile, choice: str, is_default: bool = True
) -> None:
    """Apply the choice to the profile: template, wrapper, OpenCode entry. Records every file."""
    wants_claude = choice in (CLAUDE_ONLY, BOTH)
    wants_opencode = choice in (OPENCODE_ONLY, BOTH)
    if wants_claude and not _ensure_installed(app, prompter, CLAUDE_CODE):
        wants_claude = False
    if wants_opencode and not _ensure_installed(app, prompter, OPENCODE):
        wants_opencode = False
    if wants_claude:
        _set_up_claude(app, profile, is_default)
    else:
        _drop_claude(app, profile)
    if wants_opencode:
        _set_up_opencode(app, prompter, profile)
    elif profile.integrations.get("opencode"):
        opencode.remove_entry(app, profile)
        profile.integrations.pop("opencode", None)


def _ensure_installed(app: App, prompter: PlainPrompter, installer: Installer) -> bool:
    if find(installer, app.host, app.home):
        return True
    app.say(
        f"{prompter.bold(installer.name)} (the {installer.binary} command-line app) is not "
        f"installed. Its official installer is: {installer.command}"
    )
    if not prompter.confirm(f"Install {installer.name} now?", default=True):
        app.say(f"Skipping {installer.name}.")
        return False
    if installer.wait_note:
        prompter.paragraph(f"Installing {installer.name}. {installer.wait_note}")
    binary = install(installer, app.host, app.home, path=app.env.get("PATH", ""))
    if binary is None:
        app.say(f"{installer.name} did not install. Run the command above yourself, then:")
        app.say("  ornith-launcher setup --integrations <profile>")
        return False
    # Only what the launcher installed is ever offered for removal.
    installed.record(app.paths, "tools", installer.binary)
    # The installer's own output stays in the scrollback; the questions go on afresh.
    prompter.new_screen(f"Setting up {installer.name}")
    app.say(f"Installed {prompter.bold(installer.name)}: {binary}")
    _say_how_to_start(app, installer, Path(binary))
    return True


def _say_how_to_start(app: App, installer: Installer, binary: Path) -> None:
    """Name the command, and give the PATH line when a new terminal window would not find it."""
    command = installer.binary
    app.say(
        f"  {installer.name} is a command-line app: type {command} in a new terminal window "
        "to start it (this one may not find it yet)."
    )
    shell = app.env.get("SHELL", "")
    if app.os_name == "windows" or new_window_finds(
        app.host, app.home, shell, app.os_name, command
    ) in (True, None):
        return
    app.say(
        f"  A new terminal window would not find {command}: {binary.parent} is not on its PATH."
    )
    app.say(f"    To add it: {path_line(app.home, shell, app.os_name, binary.parent)}")


def _set_up_claude(app: App, profile: Profile, is_default: bool) -> None:
    template = compat.read_template(Path(profile.model.path))
    if template is not None:
        verdict = compat.check(template)
        if verdict.needs_patch:
            target = app.paths.templates / f"{profile.name}.jinja"
            atomic_write_text(target, compat.apply(template, verdict.rule))
            profile.runtime.template = TemplateInfo(
                source="patched", rule=verdict.rule.id, sha256=verdict.sha256, file=str(target)
            )
            note = "" if verdict.tested else " (this template version is untested)"
            app.say(f"  Applied the chat-template fix {verdict.rule.id}{note}.")
        else:
            app.say(
                "  No known fix applies to this model's chat template; it is checked when the "
                "server first starts."
            )
    wrapper = claude_code.write_wrapper(app.home, profile)
    record = {"wrapper": str(wrapper)}
    # `ornith-claude` runs the default profile's wrapper.
    alias = claude_code.point_alias(app.home, wrapper) if is_default else None
    if alias is not None:
        record["alias"] = str(alias)
    profile.integrations["claude_code"] = record
    app.say(f"  Created {wrapper}. Run it to use Claude Code with this model.")
    if not claude_code.on_path(wrapper.parent, app.env):
        app.say(f"    {wrapper.parent} is not on your PATH; run it by its full path, or add it.")
        if app.os_name == "darwin":
            # zsh is the default shell on macOS; Linux shells differ too much for one line.
            app.say(f"    To add it: echo 'export PATH=\"{wrapper.parent}:$PATH\"' >> ~/.zprofile")


def _drop_claude(app: App, profile: Profile) -> None:
    record = profile.integrations.pop("claude_code", None) or {}
    remove_claude_files(record)
    template = profile.runtime.template
    if template.source == "patched" and template.file:
        Path(template.file).unlink(missing_ok=True)
    profile.runtime.template = TemplateInfo(source="gguf", sha256=template.sha256)


def _set_up_opencode(app: App, prompter: PlainPrompter, profile: Profile) -> None:
    path = opencode.config_path(app.env, app.home)
    display = claude_code.display_name(profile)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    config = {}
    if text:
        if opencode.has_comments(text) or path.suffix == ".jsonc":
            _paste_instead(app, prompter, path, profile, display, text)
            return
        try:
            config = json.loads(text)
        except ValueError:
            app.say(f"  {path} is not valid JSON, so it is left alone.")
            app.say(opencode.snippet(profile, opencode.PROVIDER, display))
            return
    provider_id = opencode.provider_id_for(config, profile)
    record = {"config": str(path), "provider": provider_id, "label": opencode.PROVIDER_NAME}
    record.update(_offer_default(prompter, profile, config, provider_id, display))
    if path.exists():
        record["backup"] = str(opencode.backup(path, datetime.datetime.now()))
    pictures = opencode.write_pictures(app.paths)
    opencode.write(path, opencode.merged(config, profile, provider_id, display, pictures))
    profile.integrations["opencode"] = record
    app.say(
        f"  Added {profile.runtime.alias} to {path}. "
        f"In OpenCode: /models -> {opencode.PROVIDER_NAME}"
    )
    _say_pictures(app, pictures)
    if record.get("default_model"):
        previous = record.get("previous_model")
        app.say(
            "  New OpenCode sessions start with it. Uninstalling puts the default back to "
            f"{previous if previous else 'none'}."
        )


def _say_pictures(app: App, pictures: Path) -> None:
    app.say(
        f"  Added picture instructions to OpenCode's instructions: {pictures}\n"
        "  OpenCode puts this file in the system prompt of every session, for every model, so\n"
        "  models know how to show charts and diagrams in the chat. You can read it there."
    )


def _offer_default(
    prompter: PlainPrompter, profile: Profile, config: dict, provider_id: str, display: str
) -> dict:
    """Ask to make the model OpenCode's default; returns what uninstall needs to undo it.

    The answer changes ``config`` in place. A default that is already this model
    (setup run again) keeps the default it replaced the first time.
    """
    ours = opencode.model_ref(provider_id, profile)
    current = config.get("model")
    if current == ours:
        earlier = profile.integrations.get("opencode") or {}
        return {"default_model": ours, "previous_model": earlier.get("previous_model")}
    now = f"now {current}" if current else "none is set now"
    if not prompter.confirm(f"Make {display} OpenCode's default model ({now})?", default=True):
        return {}
    config["model"] = ours
    return {"default_model": ours, "previous_model": current}


def _paste_instead(
    app: App, prompter: PlainPrompter, path: Path, profile: Profile, display: str, text: str
) -> None:
    provider_id = opencode.PROVIDER
    app.say(f"{path} has comments, which this tool cannot keep when it rewrites the file.")
    app.say("Add this to it yourself:")
    app.say(opencode.snippet(profile, provider_id, display))
    pictures = opencode.write_pictures(app.paths)
    app.say(
        "To make it the default model, also set "
        f'"model": "{opencode.model_ref(provider_id, profile)}". To let models show pictures '
        f'in the chat, add "{pictures}" to "instructions". OpenCode puts that file in the '
        "system prompt of every session, for every model."
    )
    if not prompter.confirm(
        "Or rewrite the file without its comments (a backup is kept)?", default=False
    ):
        return
    try:
        config = json.loads(_strip_comments(text))
    except ValueError:
        app.say("The file could not be read even without comments; it is left alone.")
        return
    record = {
        "config": str(path),
        "provider": provider_id,
        "label": opencode.PROVIDER_NAME,
        **_offer_default(prompter, profile, config, provider_id, display),
        "backup": str(opencode.backup(path, datetime.datetime.now())),
    }
    opencode.write(path, opencode.merged(config, profile, provider_id, display, pictures))
    profile.integrations["opencode"] = record
    _say_pictures(app, pictures)


def _strip_comments(text: str) -> str:
    out = []
    index = 0
    in_string = escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
            out.append(char)
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end == -1 else end
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end == -1 else end + 2
            continue
        else:
            out.append(char)
        index += 1
    return "".join(out)


def remove_claude_files(record: dict) -> None:
    """Delete the wrapper, and the ``ornith-claude`` link only while it still points to it."""
    wrapper = Path(record["wrapper"]) if record.get("wrapper") else None
    alias = Path(record["alias"]) if record.get("alias") else None
    if alias is not None and alias.is_symlink() and wrapper is not None:
        if alias.resolve() == wrapper.resolve():
            alias.unlink()
    if wrapper is not None:
        wrapper.unlink(missing_ok=True)
