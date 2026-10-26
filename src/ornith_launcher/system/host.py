"""The machine as the detection code sees it: commands, files and environment.

Detection only reads through a :class:`Host`, so tests can replace the real
machine with a fake that returns recorded command output and file contents.
"""

from __future__ import annotations

import glob as _glob
import os
import shutil
import signal
import subprocess
from collections.abc import Iterator, Mapping, Sequence
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

    def run_live(self, argv: Sequence[str]) -> int:
        """Run a command with its output shown to the user (e.g. docker pull progress)."""

    def spawn(self, argv: Sequence[str]) -> bool:
        """Start a detached process (e.g. a terminal window); False if it could not start."""

    def process_command(self, pid: int) -> str | None:
        """The command line of a running process, or None if there is no such process."""

    def terminate(self, pid: int) -> None:
        """Ask a process to end (SIGTERM); nothing happens if it is already gone."""

    def stream(self, argv: Sequence[str]) -> LineStream | None:
        """A running command's output, line by line; None if it is not installed."""


class LineStream(Protocol):
    def lines(self) -> Iterator[str]:
        """Output lines (without the line end) until the command ends or is closed."""

    def close(self) -> None: ...


class _ProcessLines:
    def __init__(self, process: subprocess.Popen) -> None:
        self.process = process

    def lines(self) -> Iterator[str]:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            yield line.rstrip("\r\n")

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.process.stdout is not None:
            self.process.stdout.close()


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

    def run_live(self, argv: Sequence[str]) -> int:
        try:
            return subprocess.call(list(argv), env=self._env)
        except (FileNotFoundError, PermissionError):
            return 127

    def spawn(self, argv: Sequence[str]) -> bool:
        # A new session, so terminals that stay in the foreground (Konsole) do not block us.
        try:
            subprocess.Popen(
                list(argv),
                env=self._env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError):
            return False
        return True

    def process_command(self, pid: int) -> str | None:
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            if os.name == "nt" or Path("/proc/self").exists():
                return None  # no such process (Linux), or not supported yet (Windows)
            result = self.run(["ps", "-o", "command=", "-p", str(pid)])  # macOS
            if result is None or not result.ok:
                return None
            return result.stdout.strip() or None
        return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip() or None

    def terminate(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    def stream(self, argv: Sequence[str]) -> LineStream | None:
        # Same process group, so closing the window (SIGHUP) or Ctrl+C ends the command too.
        try:
            process = subprocess.Popen(
                list(argv),
                env=self._env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=1,
            )
        except (FileNotFoundError, PermissionError):
            return None
        return _ProcessLines(process)
