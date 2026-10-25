"""Desktop shortcuts that launch a profile with a double-click (Linux ``.desktop`` files).

``Terminal=false``: the launcher opens the chosen terminal itself. A ``.desktop``
entry also avoids GNOME Files running a ``.sh`` inside GNOME Terminal.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path

from ornith_launcher.config import atomic_write_text


def desktop_entry(profile: str, command: Sequence[str], comment: str) -> str:
    exec_line = " ".join(_desktop_quote(part) for part in [*command, "launch", profile])
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            f"Name=Ornith: {profile}",
            f"Comment={comment}",
            f"Exec={exec_line}",
            "Terminal=false",
            "Categories=Development;",
            "X-Ornith-Launcher=1",
            "",
        ]
    )


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
    return applications_dir(env, home) / f"ornith-launcher-{profile}.desktop"


def write_shortcut(path: Path, content: str) -> None:
    atomic_write_text(path, content, mode=0o755)


def shell_command(command: Sequence[str]) -> str:
    return shlex.join(command)
