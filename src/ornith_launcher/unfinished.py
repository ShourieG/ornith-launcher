"""Downloads stopped before they finished, found from the launcher's own install record.

Setup's first menu offers to resume them (the same model, into the same folder) and
the uninstall menu offers to delete them. Only files the record lists are considered,
grouped by model folder (``<models>/<owner>/<repo>@<sha7>/``), so the finished parts of
a split model are kept or removed together with the part still downloading.

When a download starts, setup also saves what was chosen for it (context, KV cache,
parallel requests, vision, port and the confirmations given) in the launcher's state
folder, so resuming goes on with those settings instead of asking for them again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ornith_launcher.catalog.catalog import is_projector
from ornith_launcher.config import Paths, atomic_write_json, read_json
from ornith_launcher.installed import load_installed
from ornith_launcher.profiles import list_profiles, load_profile

PART, STATE = ".part", ".part.json"


@dataclass
class Unfinished:
    folder: Path  # <models>/<owner>/<repo>@<sha7>
    names: list[str] = field(default_factory=list)  # recorded file names in the folder
    done: int = 0  # bytes downloaded so far
    total: int = 0  # full size of the files started so far

    @property
    def repo_id(self) -> str:
        return f"{self.folder.parent.name}/{self.folder.name.rsplit('@', 1)[0]}"

    @property
    def sha7(self) -> str:
        return self.folder.name.rsplit("@", 1)[1] if "@" in self.folder.name else ""

    @property
    def models_dir(self) -> Path:
        return self.folder.parent.parent

    @property
    def model_name(self) -> str:
        """The model's file name (not the vision projector's), for lists."""
        names = [n for n in self.names if not is_projector(n)]
        return (names or self.names)[0]

    def files(self) -> list[Path]:
        """Everything on disk that belongs to it, finished parts included."""
        found = []
        for name in self.names:
            for suffix in ("", PART, STATE):
                path = self.folder / (name + suffix)
                if path.exists():
                    found.append(path)
        return found


def files_in_use(paths: Paths) -> set[str]:
    """Model files that a saved setup uses: never part of an unfinished download."""
    in_use: set[str] = set()
    for name in list_profiles(paths):
        model = load_profile(paths, name).model
        in_use.update(p for p in [*model.parts, model.mmproj] if p)
    return in_use


def unfinished_downloads(paths: Paths) -> list[Unfinished]:
    """Recorded downloads with a ``.part`` file; files a saved setup uses are left out."""
    in_use = files_in_use(paths)
    groups: dict[Path, Unfinished] = {}
    for recorded in load_installed(paths).downloads:
        if recorded in in_use:
            continue
        dest = Path(recorded)
        groups.setdefault(dest.parent, Unfinished(dest.parent)).names.append(dest.name)
    found = []
    for group in groups.values():
        stopped = False
        for name in group.names:
            dest = group.folder / name
            if (group.folder / (name + PART)).exists():
                stopped = True
                done, size = state_progress(group.folder / (name + STATE))
                group.done += done
                group.total += size
            elif dest.exists():
                group.done += dest.stat().st_size
                group.total += dest.stat().st_size
        if stopped:
            found.append(group)
    return found


def state_progress(state_file: Path) -> tuple[int, int]:
    """(bytes done, file size) from the downloader's resume state; (0, 0) if unreadable."""
    try:
        state = read_json(state_file) or {}
        segments = state.get("segments") or []
        return sum(int(s[2]) for s in segments), int(state.get("size") or 0)
    except (OSError, ValueError, TypeError, IndexError, AttributeError):
        return 0, 0


# The settings chosen for a download -----------------------------------------------------


def choices_file(paths: Paths, folder: Path) -> Path:
    return paths.state / "downloads" / f"{folder.parent.name}--{folder.name}.json"


def save_choices(paths: Paths, folder: Path, choices: dict[str, Any]) -> None:
    atomic_write_json(choices_file(paths, folder), {"folder": str(folder), **choices})


def load_choices(paths: Paths, folder: Path) -> dict[str, Any] | None:
    try:
        choices = read_json(choices_file(paths, folder))
    except (OSError, ValueError):
        return None
    return choices if isinstance(choices, dict) and choices.get("folder") == str(folder) else None


def forget_choices(paths: Paths, folder: Path) -> None:
    choices_file(paths, folder).unlink(missing_ok=True)
