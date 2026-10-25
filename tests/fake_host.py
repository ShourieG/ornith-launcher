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
    ) -> None:
        self._env = dict(env or {})
        self.files = dict(files or {})
        self.commands = dict(commands or {})
        self.programs = dict(programs or {})
        self.links = dict(links or {})
        self.free = dict(free or {})
        self.calls: list[tuple[str, ...]] = []

    @property
    def env(self) -> Mapping[str, str]:
        return self._env

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> CommandResult | None:
        key = tuple(argv)
        self.calls.append(key)
        if argv[0] not in self.programs:
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


def ok(stdout: str) -> CommandResult:
    return CommandResult(0, stdout, "")


def failed(stderr: str, code: int = 1) -> CommandResult:
    return CommandResult(code, "", stderr)
