"""Coding-tool windows: Claude Code and OpenCode opened next to the server, already connected.

Setup can turn this on for the tools a profile is connected to
(``terminal["open_tools"]``, with the folder they start in as
``terminal["tools_folder"]``). Each launch then opens one window per tool once the
server answers, in the same terminal app as the server window: Claude Code through
the profile's ``ornith-claude-<profile>`` wrapper, OpenCode with the profile's
model picked (``-m provider/model``).

The windows belong to the user from then on: stopping the server never closes
them, because they may hold unsaved work.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from ornith_launcher.app import App
from ornith_launcher.integrations.installers import OPENCODE, find
from ornith_launcher.profiles import Profile
from ornith_launcher.ui.prompts import Choice, PlainPrompter

CLAUDE = "claude_code"
OPENCODE_KEY = "opencode"
NAMES = {CLAUDE: "Claude Code", OPENCODE_KEY: "OpenCode"}


def connected(profile: Profile) -> list[str]:
    """The coding tools this profile is connected to, in a fixed order."""
    tools = []
    if (profile.integrations.get(CLAUDE) or {}).get("wrapper"):
        tools.append(CLAUDE)
    if (profile.integrations.get(OPENCODE_KEY) or {}).get("provider"):
        tools.append(OPENCODE_KEY)
    return tools


def chosen(profile: Profile) -> list[str]:
    """The tools to open at launch: the ones asked for that are still connected."""
    wanted = profile.terminal.get("open_tools") or []
    return [tool for tool in connected(profile) if tool in wanted]


def folder(app: App, profile: Profile) -> Path:
    saved = profile.terminal.get("tools_folder")
    return Path(saved) if saved else app.home


def tool_command(app: App, profile: Profile, tool: str) -> list[str] | None:
    """How to start one tool against this profile's model; None if it cannot be found."""
    if tool == CLAUDE:
        wrapper = (profile.integrations.get(CLAUDE) or {}).get("wrapper")
        return [wrapper] if wrapper and Path(wrapper).is_file() else None
    record = profile.integrations.get(OPENCODE_KEY) or {}
    binary = find(OPENCODE, app.host, app.home)
    if binary is None or not record.get("provider"):
        return None
    return [binary, "-m", f"{record['provider']}/{profile.runtime.alias}"]


def in_folder(command: list[str], path: Path) -> list[str]:
    """The command run from ``path``: coding tools work on the folder they start in."""
    return ["sh", "-c", f"cd {shlex.quote(str(path))} && exec {shlex.join(command)}"]


def offer(app: App, prompter: PlainPrompter, profile: Profile, headless: bool) -> None:
    """Ask which connected tools open with the model (default all of them), and where."""
    tools = connected(profile)
    if headless or not tools:
        profile.terminal.pop("open_tools", None)
        profile.terminal.pop("tools_folder", None)
        return
    prompter.new_screen("Coding tools at launch")
    names = " and ".join(NAMES[t] for t in tools)
    prompter.paragraph(
        f"Each launch can also open {names} in its own window, connected to this model, once "
        "the server is ready. Those windows stay open when the server stops, since they may "
        "hold your work."
    )
    if len(tools) == 1:
        picked = (
            tools
            if prompter.confirm(f"Open {NAMES[tools[0]]} when this model starts?", default=True)
            else []
        )
    else:
        options = [[], [CLAUDE], [OPENCODE_KEY], [CLAUDE, OPENCODE_KEY]]
        labels = ["Neither", "Claude Code", "OpenCode", "Both"]
        answer = prompter.choose(
            "Open a coding tool when this model starts?",
            ["Tool"],
            [Choice((label,)) for label in labels],
            default=3,
        )
        picked = options[int(answer)]
    if not picked:
        profile.terminal.pop("open_tools", None)
        profile.terminal.pop("tools_folder", None)
        return
    current = str(folder(app, profile))
    while True:
        typed = prompter.text("Folder they open in:", default=current).strip() or current
        path = Path(typed).expanduser()
        if path.is_dir():
            break
        app.say(f"  {path} is not a folder. Type an existing folder, or press Enter for {current}.")
    profile.terminal["open_tools"] = picked
    profile.terminal["tools_folder"] = str(path.resolve())
