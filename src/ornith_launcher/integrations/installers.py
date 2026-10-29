"""Installing Claude Code or OpenCode with their official installers, when the user asks.

The command is shown before it runs. The script is downloaded over HTTPS to a
temporary file and run with ``bash`` as the user, never with sudo, then the
tool's ``--version`` confirms it works, and a shell started the way a new terminal
window starts one shows whether the command can be found there.

Uninstalling follows each tool's own documented way: OpenCode's ``opencode
uninstall`` (then the rest of ``~/.opencode``, which its installer made and its
uninstaller leaves), and for Claude Code deleting its launcher and version files. The
tool's own settings and history stay unless the user asks for them to go too.
"""

from __future__ import annotations

import os
import shutil
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
    # Said before the installer runs, where it can sit with nothing on screen for a while.
    wait_note: str = ""
    removes: str = ""  # what uninstalling deletes, for the confirmation screen
    remove_paths: tuple[str, ...] = ()  # deleted to uninstall, relative to home
    uninstall_args: tuple[str, ...] = ()  # or the tool's own uninstaller, after its binary
    keep_args: tuple[str, ...] = ()  # given to that uninstaller to keep settings and history
    leftovers: tuple[str, ...] = ()  # what its installer made that its uninstaller leaves
    settings: str = ""  # the tool's own settings and history, deleted only when asked
    settings_paths: tuple[str, ...] = ()  # those, relative to home, when no uninstaller does it
    # The folder its installer adds to the shell's startup file, relative to home. It skips
    # that when the folder is on PATH already, which a terminal opened before an uninstall
    # still has, so the installer runs without it.
    path_dir: str = ""

    @property
    def command(self) -> str:
        return f"curl -fsSL {self.url} | bash"


CLAUDE_CODE = Installer(
    name="Claude Code",
    binary="claude",
    url="https://claude.ai/install.sh",
    known_paths=(".local/bin/claude",),
    # It fetches the program with a silent curl -fsSL.
    wait_note="Its installer shows nothing while it downloads the program; this can take a minute "
    "or two.",
    removes="~/.local/bin/claude and ~/.local/share/claude",
    remove_paths=(".local/bin/claude", ".local/share/claude"),
    settings="~/.claude and ~/.claude.json: its settings, history, logins and MCP servers",
    settings_paths=(".claude", ".claude.json"),
)
OPENCODE = Installer(
    name="OpenCode",
    binary="opencode",
    url="https://opencode.ai/install",
    known_paths=(".opencode/bin/opencode",),
    # Its progress bar starts with the download, after it has reached GitHub's servers; one
    # address that does not answer costs curl 50 s each time (measured on a home network).
    wait_note="Its installer shows progress once the download starts. Before that it can show "
    "nothing for a minute or more while it reaches GitHub's download servers.",
    removes=(
        "~/.opencode (the program and its plugin packages), its cache and state, and the PATH "
        "line its installer added"
    ),
    uninstall_args=("uninstall", "--force"),
    keep_args=("--keep-config", "--keep-data"),
    leftovers=(".opencode",),
    settings="~/.config/opencode and ~/.local/share/opencode: its settings, sessions and plugins",
    path_dir=".opencode/bin",
)
INSTALLERS = {i.binary: i for i in (CLAUDE_CODE, OPENCODE)}


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


def without_dir(path: str, directory: Path) -> str:
    """``path`` (a PATH value) without ``directory``."""
    entries = [e for e in path.split(os.pathsep) if e]
    return os.pathsep.join(e for e in entries if Path(e).expanduser() != directory)


def install(
    installer: Installer,
    host: Host,
    home: Path,
    fetch: Callable[[str], bytes] | None = None,
    path: str = "",
) -> str | None:
    """Run the official installer; returns the installed binary, or None if it failed.

    ``path``: the PATH setup runs with, which the installer gets less ``path_dir``.
    """
    script = (fetch or fetch_script)(installer.url)
    with tempfile.NamedTemporaryFile(suffix=".sh", delete=False) as handle:
        handle.write(script)
        file = Path(handle.name)
    argv = ["bash", str(file)]
    if installer.path_dir:
        argv = ["env", f"PATH={without_dir(path, home / installer.path_dir)}", *argv]
    try:
        waiting = f"The {installer.name} installer is still working."
        if host.run_shown(argv, waiting) != 0:
            return None
    finally:
        file.unlink(missing_ok=True)
    binary = find(installer, host, home)
    if binary is None:
        return None
    result = host.run([binary, "--version"], timeout=60)
    return binary if result is not None and result.ok else None


def new_window_finds(host: Host, home: Path, shell: str, os_name: str, binary: str) -> bool | None:
    """Whether a new terminal window finds ``binary``; None when that cannot be told.

    The shell starts as a terminal window starts it (login on macOS, reading the user's
    startup files) from a bare environment, so a PATH this process inherited does not count.
    """
    name = Path(shell).name
    if name not in ("zsh", "bash"):
        return None
    # A Linux desktop's terminal runs bash -i inside a session whose PATH came from the
    # login files (Ubuntu's ~/.profile adds ~/.local/bin), so either one finding it counts.
    flags = ("-lic", "-ic") if name == "bash" and os_name == "linux" else ("-lic",)
    env = ["env", "-i", f"HOME={home}", f"SHELL={shell}", "TERM=dumb", "PATH=/usr/bin:/bin"]
    answers = []
    for flag in flags:
        result = host.run([*env, shell, flag, f"command -v {binary}"], timeout=20)
        if result is None:
            answers.append(None)
            continue
        # Startup files may print their own lines first; the answer is the last one.
        lines = result.stdout.strip().splitlines()
        found = result.ok and bool(lines) and lines[-1].strip().endswith(f"/{binary}")
        if found:
            return True
        answers.append(False)
    return False if False in answers else None


def path_line(home: Path, shell: str, os_name: str, folder: Path) -> str:
    """The command that puts ``folder`` on PATH for new terminal windows."""
    name = Path(shell).name
    shown = f"$HOME/{folder.relative_to(home)}" if home in folder.parents else str(folder)
    if name == "bash":
        startup = "~/.bashrc" if os_name == "linux" else "~/.bash_profile"
    else:
        startup = "~/.zshrc"
    return f"echo 'export PATH=\"{shown}:$PATH\"' >> {startup}"


def uninstall(
    installer: Installer, host: Host, home: Path, keep_settings: bool = True
) -> str | None:
    """Remove a tool the launcher installed; None when done, else why it failed.

    ``keep_settings``: keep the tool's own settings and history (the default).
    """
    paths = installer.remove_paths
    if installer.uninstall_args:
        binary = next((home / p for p in installer.known_paths if (home / p).is_file()), None)
        if binary is not None:
            keep = installer.keep_args if keep_settings else ()
            result = host.run([str(binary), *installer.uninstall_args, *keep], timeout=120)
            if result is None or not result.ok:
                return (result.stderr.strip() if result else "") or "its uninstaller did not finish"
        paths = installer.leftovers  # only once its uninstaller has run
    if not keep_settings:
        paths = (*paths, *installer.settings_paths)
    for relative in paths:
        path = home / relative
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except OSError as exc:
            return str(exc)
    return None


def uninstall_command(installer: Installer, keep_settings: bool = True) -> str:
    """The same removal, for the user to run when it failed here (it asks before it acts)."""
    paths = installer.remove_paths
    steps = []
    if installer.uninstall_args:
        args = [a for a in installer.uninstall_args if a != "--force"]
        keep = installer.keep_args if keep_settings else ()
        steps.append(" ".join([f"~/{installer.known_paths[0]}", *args, *keep]))
        paths = installer.leftovers
    if not keep_settings:
        paths = (*paths, *installer.settings_paths)
    if paths:
        steps.append("rm -rf " + " ".join(f"~/{p}" for p in paths))
    return " && ".join(steps)
