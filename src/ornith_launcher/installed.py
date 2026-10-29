"""The record of what ornith-launcher put on the machine outside its own folders.

Uninstall reads this file (``installed.json`` next to the settings) together with
the profiles, so it only ever removes what the launcher itself created: model
files it downloaded, desktop icons and applications-menu entries it wrote, and
the llama.cpp image it pulled (on macOS, the llama.cpp runtime folder it
downloaded). Anything not recorded here, such as models found elsewhere or an
image that was already on the machine, is never touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ornith_launcher.config import Paths, atomic_write_json, read_json

LEDGER = "installed.json"
KINDS = ("downloads", "shortcuts", "images", "runtimes")


@dataclass
class Installed:
    downloads: list[str] = field(default_factory=list)  # model files, recorded before they finish
    shortcuts: list[str] = field(default_factory=list)  # desktop icons and menu entries
    images: list[str] = field(default_factory=list)  # Docker references the launcher pulled
    runtimes: list[str] = field(default_factory=list)  # native llama.cpp builds it unpacked

    @property
    def empty(self) -> bool:
        return not (self.downloads or self.shortcuts or self.images or self.runtimes)


def ledger_file(paths: Paths) -> Path:
    return paths.config / LEDGER


def load_installed(paths: Paths) -> Installed:
    try:
        raw = read_json(ledger_file(paths)) or {}
    except ValueError:
        raw = {}  # a damaged record only means less is offered for removal
    return Installed(**{kind: [str(v) for v in raw.get(kind, [])] for kind in KINDS})


def downloaded_here(paths: Paths, files: list[str]) -> bool:
    """Whether every one of these files is a download this launcher recorded as its own."""
    recorded = {str(Path(path).resolve()) for path in load_installed(paths).downloads}
    return bool(files) and all(str(Path(path).resolve()) in recorded for path in files)


def record(paths: Paths, kind: str, value: str) -> None:
    installed = load_installed(paths)
    items = getattr(installed, kind)
    if value not in items:
        items.append(value)
        _save(paths, installed)


def forget(paths: Paths, kind: str, value: str) -> None:
    installed = load_installed(paths)
    items = getattr(installed, kind)
    if value in items:
        items.remove(value)
        _save(paths, installed)


def _save(paths: Paths, installed: Installed) -> None:
    atomic_write_json(ledger_file(paths), {kind: getattr(installed, kind) for kind in KINDS})
