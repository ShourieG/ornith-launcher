"""The record of what ornith-launcher put on the machine outside its own folders.

Uninstall reads this file (``installed.json`` next to the settings) together with
the profiles, so it only ever removes what the launcher itself created: model
files it downloaded, desktop icons and applications-menu entries it wrote, and
the llama.cpp image it pulled. Anything not recorded here, such as models found
elsewhere or an image that was already on the machine, is never touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ornith_launcher.config import Paths, atomic_write_json, read_json

LEDGER = "installed.json"
KINDS = ("downloads", "shortcuts", "images")


@dataclass
class Installed:
    downloads: list[str] = field(default_factory=list)  # model files, recorded before they finish
    shortcuts: list[str] = field(default_factory=list)  # desktop icons and menu entries
    images: list[str] = field(default_factory=list)  # Docker references the launcher pulled

    @property
    def empty(self) -> bool:
        return not (self.downloads or self.shortcuts or self.images)


def ledger_file(paths: Paths) -> Path:
    return paths.config / LEDGER


def load_installed(paths: Paths) -> Installed:
    try:
        raw = read_json(ledger_file(paths)) or {}
    except ValueError:
        raw = {}  # a damaged record only means less is offered for removal
    return Installed(**{kind: [str(v) for v in raw.get(kind, [])] for kind in KINDS})


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
