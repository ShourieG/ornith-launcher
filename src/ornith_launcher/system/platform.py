"""Operating system, CPU architecture and desktop session."""

from __future__ import annotations

import platform as _platform
from dataclasses import dataclass

from ornith_launcher.system.host import Host

_ARCH_ALIASES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


@dataclass(frozen=True)
class PlatformInfo:
    os: str  # "linux", "darwin" or "windows"
    arch: str  # "amd64", "arm64", or the raw machine name
    desktop: str | None  # e.g. "GNOME", "KDE"; None without a desktop session
    session_type: str | None  # "x11", "wayland", "tty", ...

    @property
    def headless(self) -> bool:
        """No display: only "this terminal" can be used, and no desktop shortcuts."""
        return self.desktop is None


def normalise_arch(machine: str) -> str:
    return _ARCH_ALIASES.get(machine.lower(), machine.lower())


def detect_platform(
    host: Host, system: str | None = None, machine: str | None = None
) -> PlatformInfo:
    env = host.env
    os_name = (system or _platform.system()).lower()
    if os_name == "darwin":
        # The Mac's own desktop is always there, except over SSH, where no window can open.
        remote = bool(env.get("SSH_CONNECTION") or env.get("SSH_TTY"))
        return PlatformInfo(
            os=os_name,
            arch=normalise_arch(machine or _platform.machine()),
            desktop=None if remote else "macOS",
            session_type="ssh" if remote else "aqua",
        )
    has_display = bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    session = env.get("XDG_SESSION_TYPE") or None
    if session is None and has_display:
        session = "wayland" if env.get("WAYLAND_DISPLAY") else "x11"
    desktop = None
    if has_display:
        # A colon-separated list such as "ubuntu:GNOME"; the last entry is the base desktop.
        desktop = (env.get("XDG_CURRENT_DESKTOP") or "unknown").split(":")[-1] or "unknown"
    return PlatformInfo(
        os=os_name,
        arch=normalise_arch(machine or _platform.machine()),
        desktop=desktop,
        session_type=session,
    )
