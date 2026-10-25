"""Where ornith-launcher keeps its files, and safe ways to write them.

Linux and macOS use the XDG folders (``~/.config/ornith-launcher`` and so on);
Windows uses ``%APPDATA%`` and ``%LOCALAPPDATA%``. Downloaded models go to
``~/.ornith-launcher/models`` on every OS unless the user picks another folder.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP = "ornith-launcher"


@dataclass(frozen=True)
class Paths:
    config: Path  # settings.json, profiles/
    data: Path  # templates, runtimes
    cache: Path  # catalog, headers
    state: Path  # logs, run files
    default_models: Path

    @property
    def settings_file(self) -> Path:
        return self.config / "settings.json"

    @property
    def profiles(self) -> Path:
        return self.config / "profiles"

    @property
    def catalog_file(self) -> Path:
        return self.cache / "catalog.json"

    @property
    def templates(self) -> Path:
        return self.data / "templates"

    @property
    def logs(self) -> Path:
        return self.state / "logs"


def resolve_paths(env: Mapping[str, str], home: Path, os_name: str) -> Paths:
    """Folders for the given environment, home folder and OS ("linux", "darwin", "windows")."""
    if os_name == "windows":
        roaming = Path(env.get("APPDATA") or home / "AppData" / "Roaming") / APP
        local = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / APP
        return Paths(
            config=roaming,
            data=local,
            cache=local / "cache",
            state=local,
            default_models=home / f".{APP}" / "models",
        )

    def xdg(name: str, default: Path) -> Path:
        value = env.get(name)
        # The XDG spec says relative values are invalid and must be ignored.
        return Path(value) / APP if value and Path(value).is_absolute() else default / APP

    return Paths(
        config=xdg("XDG_CONFIG_HOME", home / ".config"),
        data=xdg("XDG_DATA_HOME", home / ".local" / "share"),
        cache=xdg("XDG_CACHE_HOME", home / ".cache"),
        state=xdg("XDG_STATE_HOME", home / ".local" / "state"),
        default_models=home / f".{APP}" / "models",
    )


def default_paths() -> Paths:
    import platform

    return resolve_paths(os.environ, Path.home(), platform.system().lower())


def atomic_write_text(path: Path, text: str, mode: int | None = None) -> None:
    """Write a file so a crash leaves either the old or the new contents, never half of each."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None and path.exists():
        mode = path.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: Any, mode: int | None = None) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=False) + "\n", mode)


def read_json(path: Path) -> Any | None:
    """Parsed JSON from ``path``, or None if the file does not exist."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return json.loads(text)
