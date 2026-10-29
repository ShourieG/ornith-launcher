"""The machine as the detection code sees it: commands, files and environment.

Detection only reads through a :class:`Host`, so tests can replace the real
machine with a fake that returns recorded command output and file contents.
"""

from __future__ import annotations

import glob as _glob
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
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

    def metal_working_set(self) -> int | None:
        """Metal's working-set limit in bytes on a Mac; None elsewhere or if unknown."""

    def run_live(self, argv: Sequence[str]) -> int:
        """Run a command with its output shown to the user (e.g. docker pull progress)."""

    def run_shown(self, argv: Sequence[str], waiting: str) -> int:
        """``run_live``, plus a spinner with ``waiting`` while the command prints nothing.

        For installers that sit silent while they connect or download, so the screen does
        not look frozen. The command's own output and progress bars are shown unchanged.
        """

    def spawn(self, argv: Sequence[str]) -> bool:
        """Start a detached process (e.g. a terminal window); False if it could not start."""

    def process_command(self, pid: int) -> str | None:
        """The command line of a running process, or None if there is no such process."""

    def terminate(self, pid: int) -> None:
        """Ask a process to end (SIGTERM); nothing happens if it is already gone."""

    def kill(self, pid: int) -> None:
        """End a process that did not stop when asked (SIGKILL)."""

    def start_process(self, argv: Sequence[str], log: Path, cwd: Path | None = None) -> int | None:
        """Start a process in its own session with its output in ``log``; its pid, or None.

        Its own session means Ctrl+C in this terminal, or closing the window, does not reach it.
        """

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
        self._children: dict[int, subprocess.Popen] = {}  # started here, reaped when they end

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

    def metal_working_set(self) -> int | None:
        if sys.platform != "darwin":
            return None
        from ornith_launcher.system import macos

        return macos.read_working_set()

    def run_live(self, argv: Sequence[str]) -> int:
        try:
            return subprocess.call(list(argv), env=self._env)
        except (FileNotFoundError, PermissionError):
            return 127

    def run_shown(self, argv: Sequence[str], waiting: str) -> int:
        try:
            import fcntl
            import pty
            import termios
        except ImportError:  # Windows: no pseudo-terminals
            return self.run_live(argv)
        if not sys.stdout.isatty():
            return self.run_live(argv)  # a log or pipe: no spinner to draw
        master, slave = pty.openpty()
        try:
            size = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, size)  # its progress bars fit the window
        except OSError:
            pass
        try:
            # Same process group, so Ctrl+C in this terminal reaches it too. No input: the
            # installers it runs ask nothing, and a question would otherwise wait forever.
            process = subprocess.Popen(
                list(argv), env=self._env, stdin=subprocess.DEVNULL, stdout=slave, stderr=slave
            )
        except (FileNotFoundError, PermissionError):
            os.close(master)
            return 127
        finally:
            os.close(slave)
        out = sys.stdout.buffer

        def write(data: bytes) -> None:
            out.write(data)
            out.flush()

        try:
            relay(master, process.poll, write, waiting)
            return process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
            os.close(master)

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
        child = self._children.get(pid)
        if child is not None and child.poll() is not None:
            del self._children[pid]  # ended: poll() reaped it, so it is no zombie
            return None
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

    def kill(self, pid: int) -> None:
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except OSError:
            pass

    def start_process(self, argv: Sequence[str], log: Path, cwd: Path | None = None) -> int | None:
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(log, "wb") as handle:
                process = subprocess.Popen(
                    list(argv),
                    env=self._env,
                    cwd=str(cwd) if cwd else None,
                    stdin=subprocess.DEVNULL,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError:
            return None
        self._children[process.pid] = process
        return process.pid

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


# Seconds without output before the spinner shows, and the spinner's frames.
QUIET = 2.0
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def relay(
    fd: int,
    poll: Callable[[], int | None],
    write: Callable[[bytes], None],
    waiting: str,
    clock: Callable[[], float] = time.monotonic,
    quiet: float = QUIET,
) -> None:
    """Copy a command's output from ``fd`` to ``write`` until it ends.

    While it prints nothing for ``quiet`` seconds, and its last line is complete (a
    progress bar drawn in place is not), a spinner line shows ``waiting`` and the time
    since the start; it is cleared before the command's next output.
    """
    start = last = clock()
    at_line_start, shown, frame = True, False, 0
    while True:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                data = os.read(fd, 4096)
            except OSError:  # Linux: the command closed its end of the terminal
                data = b""
            if not data:
                break
            if shown:
                write(b"\r\x1b[K")
                shown = False
            write(data)
            at_line_start = data.endswith(b"\n")
            last = clock()
        elif poll() is not None:
            break
        elif at_line_start and clock() - last >= quiet:
            elapsed = int(clock() - start)
            spin = SPINNER[frame % len(SPINNER)]
            frame += 1
            line = f"\r  {spin} {waiting} ({elapsed // 60}:{elapsed % 60:02d})\x1b[K"
            write(line.encode("utf-8"))
            shown = True
    if shown:
        write(b"\r\x1b[K")
