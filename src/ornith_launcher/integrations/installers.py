"""Installing Claude Code or OpenCode with their official installers, when the user asks.

The command is shown before it runs. The script is downloaded over HTTPS to a
temporary file and run with ``bash`` as the user, never with sudo, then the
tool's ``--version`` confirms it works.
"""

from __future__ import annotations

import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ornith_launcher.system.host import Host


@dataclass(frozen=True)
class Installer:
    name: str
    binary: str
    url: str
    known_paths: tuple[str, ...]  # where the installer puts the binary, relative to home

    @property
    def command(self) -> str:
        return f"curl -fsSL {self.url} | bash"


CLAUDE_CODE = Installer(
    name="Claude Code",
    binary="claude",
    url="https://claude.ai/install.sh",
    known_paths=(".local/bin/claude",),
)
OPENCODE = Installer(
    name="OpenCode",
    binary="opencode",
    url="https://opencode.ai/install",
    known_paths=(".opencode/bin/opencode",),
)


def find(installer: Installer, host: Host, home: Path) -> str | None:
    found = host.which(installer.binary)
    if found:
        return found
    for relative in installer.known_paths:
        path = home / relative
        if path.is_file():
            return str(path)
    return None


def fetch_script(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as reply:
        return reply.read()


def install(
    installer: Installer,
    host: Host,
    home: Path,
    fetch: Callable[[str], bytes] | None = None,
) -> str | None:
    """Run the official installer; returns the installed binary, or None if it failed."""
    script = (fetch or fetch_script)(installer.url)
    with tempfile.NamedTemporaryFile(suffix=".sh", delete=False) as handle:
        handle.write(script)
        path = Path(handle.name)
    try:
        if host.run_live(["bash", str(path)]) != 0:
            return None
    finally:
        path.unlink(missing_ok=True)
    binary = find(installer, host, home)
    if binary is None:
        return None
    result = host.run([binary, "--version"], timeout=60)
    return binary if result is not None and result.ok else None
