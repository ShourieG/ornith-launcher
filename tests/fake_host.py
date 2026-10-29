"""A fake machine for detection tests: recorded command output, files and environment."""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping, Sequence

from ornith_launcher.system.host import CommandResult


class FakeHost:
    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        files: Mapping[str, str] | None = None,
        commands: Mapping[tuple[str, ...], CommandResult | None] | None = None,
        programs: Mapping[str, str] | None = None,
        links: Mapping[str, str] | None = None,
        free: Mapping[str, int] | None = None,
        metal: int | None = None,
    ) -> None:
        self._env = dict(env or {})
        self.files = dict(files or {})
        self.commands = dict(commands or {})
        self.programs = dict(programs or {})
        self.links = dict(links or {})
        self.free = dict(free or {})
        self.metal = metal  # Metal's working-set limit; None: Metal could not be asked
        self.calls: list[tuple[str, ...]] = []
        self.live: list[tuple[str, ...]] = []
        self.spawned: list[tuple[str, ...]] = []
        self.live_codes: dict[tuple[str, ...], int] = {}
        self.processes: dict[int, str] = {}  # pid -> command line
        self.terminated: list[int] = []
        self.killed: list[int] = []
        self.ignore_terminate: set[int] = set()  # pids that do not end on SIGTERM
        self.started: list = []  # (argv, log, cwd) per start_process
        self.start_fails = False
        self.start_log = ""  # what a started process writes to its log
        self.next_pid = 4000
        self.streamed: list[tuple[str, ...]] = []
        self.stream_lines: dict[tuple[str, ...], list[str]] = {}  # command -> its output

    @property
    def env(self) -> Mapping[str, str]:
        return self._env

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> CommandResult | None:
        key = tuple(argv)
        self.calls.append(key)
        if argv[0] not in self.programs and argv[0] not in self.programs.values():
            return None
        return self.commands.get(key, CommandResult(127, "", f"{argv[0]}: unexpected arguments"))

    def which(self, name: str) -> str | None:
        return self.programs.get(name)

    def read_text(self, path: str) -> str | None:
        return self.files.get(path)

    def exists(self, path: str) -> bool:
        return path in self.files or any(f.startswith(path.rstrip("/") + "/") for f in self.files)

    def glob(self, pattern: str) -> list[str]:
        candidates = set()
        depth = pattern.count("/")
        for path in self.files:
            parts = path.split("/")
            # Every ancestor folder of a file can match a pattern too.
            for end in range(2, len(parts) + 1):
                prefix = "/".join(parts[:end])
                if prefix.count("/") == depth:
                    candidates.add(prefix)
        return sorted(c for c in candidates if fnmatch.fnmatchcase(c, pattern))

    def realpath(self, path: str) -> str:
        return self.links.get(path, path)

    def disk_free(self, path: str) -> int | None:
        return self.free.get(path)

    def metal_working_set(self) -> int | None:
        return self.metal

    def run_live(self, argv: Sequence[str]) -> int:
        self.live.append(tuple(argv))
        return self.live_codes.get(tuple(argv), 0)

    def run_shown(self, argv: Sequence[str], waiting: str) -> int:
        self.waiting = waiting
        return self.run_live(argv)

    def spawn(self, argv: Sequence[str]) -> bool:
        self.spawned.append(tuple(argv))
        return argv[0] in self.programs or argv[0].startswith("/")

    def process_command(self, pid: int) -> str | None:
        return self.processes.get(pid)

    def terminate(self, pid: int) -> None:
        self.terminated.append(pid)
        if pid not in self.ignore_terminate:
            self.processes.pop(pid, None)

    def kill(self, pid: int) -> None:
        self.killed.append(pid)
        self.processes.pop(pid, None)

    def start_process(self, argv, log, cwd=None):
        self.started.append((tuple(argv), log, cwd))
        if self.start_fails:
            return None
        pid = self.next_pid
        self.next_pid += 1
        self.processes[pid] = " ".join(argv)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(self.start_log)
        return pid

    def stream(self, argv: Sequence[str]):
        self.streamed.append(tuple(argv))
        lines = self.stream_lines.get(tuple(argv))
        return None if lines is None else FakeStream(lines)


class FakeStream:
    def __init__(self, lines):
        self._lines = list(lines)
        self.closed = False

    def lines(self):
        yield from self._lines  # all of them, closed or not, so tests see a fixed output

    def close(self):
        self.closed = True


def ok(stdout: str) -> CommandResult:
    return CommandResult(0, stdout, "")


def failed(stderr: str, code: int = 1) -> CommandResult:
    return CommandResult(code, "", stderr)
