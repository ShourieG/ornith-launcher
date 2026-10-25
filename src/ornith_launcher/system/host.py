"""The machine as the detection code sees it: commands, files and environment.

Detection only reads through a :class:`Host`, so tests can replace the real
machine with a fake that returns recorded command output and file contents.
"""

from __future__ import annotations

import glob as _glob
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Host(Protocol):
    @property
    def env(self) -> Mapping[str, str]: ...

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> CommandResult | None:
        """Run a command; None if it is not installed or did not finish in time."""

    def which(self, name: str) -> str | None: ...

    def read_text(self, path: str) -> str | None:
        """Contents of a file, or None if it is missing or unreadable."""

    def exists(self, path: str) -> bool: ...

    def glob(self, pattern: str) -> list[str]: ...

    def realpath(self, path: str) -> str: ...

    def disk_free(self, path: str) -> int | None:
        """Free bytes on the file system holding ``path``, or None if unknown."""


class LocalHost:
    """The machine this process runs on."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = dict(os.environ if env is None else env)

    @property
    def env(self) -> Mapping[str, str]:
        return self._env

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> CommandResult | None:
        try:
            done = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                env=self._env,
                stdin=subprocess.DEVNULL,
            )
        except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
            return None
        return CommandResult(done.returncode, done.stdout, done.stderr)

    def which(self, name: str) -> str | None:
        return shutil.which(name, path=self._env.get("PATH"))

    def read_text(self, path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def glob(self, pattern: str) -> list[str]:
        return sorted(_glob.glob(pattern))

    def realpath(self, path: str) -> str:
        return os.path.realpath(path)

    def disk_free(self, path: str) -> int | None:
        try:
            return shutil.disk_usage(path).free
        except OSError:
            return None
