"""What every command needs from the outside world, gathered in one object tests can replace."""

from __future__ import annotations

import os
import platform
import shutil
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from ornith_launcher.config import Paths, resolve_paths
from ornith_launcher.system.host import Host, LocalHost


@dataclass
class App:
    host: Host
    paths: Paths
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)
    clock: Callable[[], float] = time.time
    os_name: str = field(default_factory=lambda: platform.system().lower())
    machine: str = field(default_factory=platform.machine)
    read_line: Callable[[str], str] = input
    full_screen: bool | None = None  # None: decide from the terminal

    @property
    def env(self) -> Mapping[str, str]:
        return self.host.env

    @property
    def home(self) -> Path:
        home = self.env.get("HOME") or self.env.get("USERPROFILE")
        return Path(home) if home else Path.home()

    @classmethod
    def default(cls) -> App:
        env = dict(os.environ)
        os_name = platform.system().lower()
        return cls(host=LocalHost(env), paths=resolve_paths(env, Path.home(), os_name))

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def warn(self, text: str) -> None:
        print(text, file=self.err)


def self_command() -> list[str]:
    """How to run this launcher again, from a new terminal window or a desktop shortcut."""
    script = Path(__file__).resolve().parents[2] / "ornith-launcher"  # a plain checkout
    if script.is_file() and os.access(script, os.X_OK):
        return [str(script)]
    installed = shutil.which("ornith-launcher")  # pipx install
    if installed:
        return [installed]
    return [sys.executable, "-m", "ornith_launcher"]
