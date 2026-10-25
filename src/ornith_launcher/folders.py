"""Checks for the models folder the user picks for downloads (setup screen 4b).

Models are tens of GB, so the folder must be writable, have room, sit on a file
system that holds files over 4 GiB, and preferably not be synced to a cloud
service, which would try to upload every download.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

GIB = 1024**3
SPARE = 2 * GIB

_CLOUD_FOLDERS = (
    "OneDrive",
    "Dropbox",
    "Google Drive",
    "GoogleDrive",
    "iCloud Drive",
    "iCloudDrive",
)
_CLOUD_MARKERS = ("Mobile Documents", "CloudStorage")  # macOS iCloud and File Provider folders
_FAT32 = ("vfat", "msdos", "fat", "fat32")
_SLOW = {
    "exfat": "exFAT",
    "nfs": "a network share",
    "nfs4": "a network share",
    "cifs": "a network share",
    "smb3": "a network share",
    "smbfs": "a network share",
    "fuse.sshfs": "a network share",
}


@dataclass
class FolderCheck:
    folder: Path
    free: int | None = None
    needed: int = 0
    errors: list[str] = field(default_factory=list)  # the folder cannot be used
    warnings: list[str] = field(default_factory=list)  # usable after the user agrees
    hidden_notice: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def check_models_folder(
    folder: Path,
    download_size: int,
    *,
    home: Path,
    os_name: str,
    mounts: Sequence[tuple[str, str]] = (),
    disk_free: Callable[[Path], int | None] | None = None,
) -> FolderCheck:
    """Check ``folder`` for a download of ``download_size`` bytes.

    ``mounts`` is a list of (mount point, file system type), e.g. from ``/proc/mounts``.
    """
    folder = folder.expanduser()
    check = FolderCheck(folder=folder, needed=download_size + SPARE)
    existing = _existing_parent(folder)
    if existing is None:
        check.errors.append(f"{folder} cannot be created: none of its parent folders exist.")
        return check
    if not existing.is_dir():
        check.errors.append(f"{existing} is a file, not a folder.")
        return check
    if not os.access(existing, os.W_OK):
        check.errors.append(
            f"{existing} is not writable by this user. Pick a folder under your home folder, "
            f"or fix its permissions."
        )
    free = (disk_free or _disk_free)(existing)
    check.free = free
    if free is not None and free < check.needed:
        check.errors.append(
            f"Not enough free space: the download needs {download_size / GIB:.1f} GiB plus "
            f"2 GiB spare, but {existing} has {free / GIB:.1f} GiB free."
        )
    fs_type = file_system(existing, mounts)
    if fs_type in _FAT32:
        check.errors.append(
            f"{existing} is on a FAT32 file system, which cannot hold files over 4 GiB. "
            "Pick a folder on another drive."
        )
    elif fs_type in _SLOW:
        check.warnings.append(
            f"{existing} is on {_SLOW[fs_type]}; loading a model from it will be slow."
        )
    cloud = cloud_service(folder)
    if cloud:
        check.warnings.append(
            f"{folder} is inside {cloud}, which would try to upload tens of GB. "
            "A folder outside it is better."
        )
    check.hidden_notice = hidden_notice(folder, home, os_name)
    return check


def _existing_parent(folder: Path) -> Path | None:
    for candidate in (folder, *folder.parents):
        if candidate.exists():
            return candidate
    return None


def _disk_free(path: Path) -> int | None:
    import shutil

    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def read_mounts(text: str) -> list[tuple[str, str]]:
    """Mount points and file system types from ``/proc/mounts``."""
    mounts = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 3:
            # /proc/mounts escapes spaces in paths as \040.
            mounts.append((fields[1].replace("\\040", " "), fields[2]))
    return mounts


def file_system(path: Path, mounts: Sequence[tuple[str, str]]) -> str | None:
    """The file system type of the longest mount point containing ``path``."""
    best: tuple[int, str] | None = None
    text = str(path.resolve())
    for point, fs_type in mounts:
        prefix = point.rstrip("/") + "/"
        if text == point or text.startswith(prefix) or point == "/":
            if best is None or len(point) > best[0]:
                best = (len(point), fs_type.lower())
    return best[1] if best else None


def cloud_service(folder: Path) -> str | None:
    for part in folder.parts:
        for name in _CLOUD_FOLDERS:
            if part == name or part.startswith(f"{name} -") or part.startswith(f"{name}-"):
                return name
        if part in _CLOUD_MARKERS:
            return "iCloud Drive" if part == "Mobile Documents" else "a cloud storage folder"
    return None


def hidden_notice(folder: Path, home: Path, os_name: str) -> str | None:
    """Plain words for a hidden models folder: how to open it, and that it can be changed."""
    if os_name == "windows":
        return None  # a leading dot does not hide a folder on Windows
    try:
        relative = folder.relative_to(home)
    except ValueError:
        relative = folder
    hidden = any(part.startswith(".") for part in relative.parts) or (
        os_name == "darwin" and relative.parts[:1] == ("Library",)
    )
    if not hidden:
        return None
    how = (
        "in Finder, press Cmd+Shift+. to show hidden folders"
        if os_name == "darwin"
        else "in Files, press Ctrl+H to show hidden folders"
    )
    visible = home / "ornith-models"
    return (
        f"This folder is hidden, so it won't show in your file manager by default ({how}). "
        f"You are free to pick a visible folder instead, for example {visible}."
    )


def ensure_folder(folder: Path) -> None:
    """Create the folder and prove it is writable with a temporary file."""
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=folder, prefix=".write-test-"):
        pass
