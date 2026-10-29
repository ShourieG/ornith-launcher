"""Desktop shortcuts that launch a profile with a double-click: Linux ``.desktop`` files,
and on macOS ``.command`` files, which Finder opens in Terminal.

``Terminal=false`` when a terminal was chosen: the launcher opens it itself. A
profile that runs in "this terminal" gets ``Terminal=true`` instead, or a click
would start the server with no window to watch or stop it from. A ``.desktop``
entry also avoids GNOME Files running a ``.sh`` inside GNOME Terminal.

With the chat log on, the entry gets a "Show chat log" action (right-click on
the icon or the menu entry, where the desktop shows actions).
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path

from ornith_launcher.config import atomic_write_text


def desktop_entry(
    profile: str,
    command: Sequence[str],
    comment: str,
    in_terminal: bool = False,
    chat_action: bool = False,
) -> str:
    exec_line = " ".join(_desktop_quote(part) for part in [*command, "launch", profile])
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name=Ornith: {profile}",
        f"Comment={comment}",
        f"Exec={exec_line}",
        f"Terminal={'true' if in_terminal else 'false'}",
        "Icon=utilities-terminal",  # a stock theme icon, no third-party logo
        "Categories=Development;",
        "X-Ornith-Launcher=1",
    ]
    if chat_action:
        # In a terminal the chat log shows there; otherwise the launcher opens its own window.
        chat = [*command, "chat-log", profile] + ([] if in_terminal else ["--window"])
        lines[-1:-1] = ["Actions=chat-log;"]
        lines += [
            "",
            "[Desktop Action chat-log]",
            "Name=Show chat log",
            f"Exec={' '.join(_desktop_quote(part) for part in chat)}",
        ]
    return "\n".join([*lines, ""])


def _desktop_quote(arg: str) -> str:
    # The Desktop Entry spec quotes with double quotes and escapes ", `, $ and \\.
    if arg and all(c.isalnum() or c in "-_./=:+@" for c in arg):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'


def applications_dir(env: Mapping[str, str], home: Path) -> Path:
    data = env.get("XDG_DATA_HOME")
    base = Path(data) if data and Path(data).is_absolute() else home / ".local" / "share"
    return base / "applications"


def shortcut_path(profile: str, env: Mapping[str, str], home: Path) -> Path:
    return applications_dir(env, home) / shortcut_name(profile)


def shortcut_name(profile: str) -> str:
    return f"ornith-launcher-{profile}.desktop"


def user_dirs_file(env: Mapping[str, str], home: Path) -> Path:
    config = env.get("XDG_CONFIG_HOME")
    base = Path(config) if config and Path(config).is_absolute() else home / ".config"
    return base / "user-dirs.dirs"


def desktop_dir(user_dirs: str | None, home: Path) -> Path | None:
    """The desktop folder named in ``user-dirs.dirs`` (else ~/Desktop); None if it is turned off.

    The XDG convention turns the desktop off by setting it to the home folder itself.
    """
    for line in (user_dirs or "").splitlines():
        key, _, value = line.strip().partition("=")
        if key == "XDG_DESKTOP_DIR":
            value = value.strip().strip('"')
            if value.startswith("$HOME"):
                value = str(home) + value[len("$HOME") :]
            path = Path(value)
            if not path.is_absolute() or path == home:
                return None
            return path
    return home / "Desktop"


def write_shortcut(path: Path, content: str) -> None:
    atomic_write_text(path, content, mode=0o755)


def shell_command(command: Sequence[str]) -> str:
    return shlex.join(command)


# macOS ----------------------------------------------------------------------------------


def command_file(profile: str, command: Sequence[str]) -> str:
    """A ``.command`` file: Finder runs it in Terminal, and launch opens the chosen terminal."""
    return (
        "#!/bin/sh\n"
        f"# Ornith: {profile}, made by ornith-launcher (unofficial); safe to delete.\n"
        f"exec {shell_command([*command, 'launch', profile])}\n"
    )


def command_file_name(profile: str) -> str:
    return f"Ornith {profile}.command"


def mac_applications_dir(home: Path) -> Path:
    return home / "Applications"
