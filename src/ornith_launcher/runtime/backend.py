"""The interface every runtime backend implements.

Phase 1 runs llama.cpp in Docker on Linux (``runtime.docker``). Windows and
macOS will run the native release binaries as a child process instead, behind
the same interface, so the launch, status and monitor code does not change.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ornith_launcher.profiles import Profile
from ornith_launcher.runtime.docker import Container, FitCheck
from ornith_launcher.system.host import LineStream


class Backend(Protocol):
    def start(self, profile: Profile, proxied: bool = False) -> str:
        """Start the server; "reused" when it already runs with these settings."""

    def stop(self, profile: Profile) -> bool: ...

    def status(self, profile: Profile) -> Container: ...

    def logs(self, name: str, tail: int = 30) -> str: ...

    def follow_logs(self, profile: Profile, tail: int) -> int:
        """Print the log live until the server stops; returns the follower's exit code."""

    def log_stream(self, profile: Profile) -> LineStream | None:
        """New log lines as they come, until closed or the server stops."""

    def host_port(self, profile: Profile, cached: bool = True) -> int | None:
        """The port the server itself listens on (behind the chat log proxy, if any)."""

    def api_url(self, profile: Profile) -> str:
        """The server's own URL, for the launcher's checks."""

    def wait_ready(
        self, profile: Profile, on_line: Callable[[str], None] | None = None, timeout: float = ...
    ) -> float: ...

    def warm_up(self, profile: Profile) -> float: ...

    def verify(self, profile: Profile) -> list[str]: ...

    def fit_check(self, profile: Profile) -> FitCheck | None: ...

    def requests_in_progress(self, profile: Profile) -> int | None: ...
