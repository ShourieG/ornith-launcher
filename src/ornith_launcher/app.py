"""What every command needs from the outside world, gathered in one object tests can replace."""

from __future__ import annotations

import os
import platform
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

    @property
    def env(self) -> Mapping[str, str]:
        return self.host.env

    @classmethod
    def default(cls) -> App:
        env = dict(os.environ)
        os_name = platform.system().lower()
        return cls(host=LocalHost(env), paths=resolve_paths(env, Path.home(), os_name))

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def warn(self, text: str) -> None:
        print(text, file=self.err)
