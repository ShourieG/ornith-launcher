"""Terminal windows for the server monitor: which ones exist, the default, and how to open one.

The launcher opens the chosen terminal detached (a new session), because some
terminals stay in the foreground and would block it, and passes everything on
the command line, because Konsole may hand the command to an existing process
with a different environment.

On macOS, Terminal is given a small ``.command`` script to open (``open -a
Terminal``), which needs no AppleScript, so macOS never asks for Automation
permission. iTerm2 asks "OK to run ...?" before every file it is handed that way,
so it gets the same script typed into a new window through its AppleScript
instead: macOS asks once whether the app running the launcher may control iTerm2.
The script deletes itself as it starts.
"""

from __future__ import annotations

import os
import platform
import shlex
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ornith_launcher.system.host import Host

THIS_TERMINAL = "this"


@dataclass(frozen=True)
class Terminal:
    id: str
    name: str
    argv: tuple[str, ...]  # prefix before the command; "{title}" is replaced
    opens_file: bool = False  # macOS apps: given a .command script instead of the command
    via_applescript: bool = False  # iTerm2: the script is typed into a new window instead
    failure_hint: str = ""  # what to do when the window did not open, for the message

    def command(self, command: Sequence[str], title: str) -> list[str]:
        prefix = [part.replace("{title}", title) for part in self.argv]
        return prefix + list(command)


# Known terminals and how each runs a command (design 11.4). Order = fallback preference.
KNOWN = (
    Terminal("gnome-terminal", "GNOME Terminal", ("gnome-terminal", "--title={title}", "--")),
    Terminal("konsole", "Konsole", ("konsole", "-p", "tabtitle={title}", "-e")),
    Terminal("ptyxis", "Ptyxis", ("ptyxis", "--")),
    Terminal("kgx", "GNOME Console", ("kgx", "--")),
    Terminal("kitty", "kitty", ("kitty", "--title", "{title}")),
    Terminal("alacritty", "Alacritty", ("alacritty", "--title", "{title}", "-e")),
    Terminal("wezterm", "WezTerm", ("wezterm", "start", "--")),
    Terminal("foot", "foot", ("foot", "--title={title}")),
    Terminal("xfce4-terminal", "Xfce Terminal", ("xfce4-terminal", "--title={title}", "-x")),
    Terminal("tilix", "Tilix", ("tilix", "-t", "{title}", "-e")),
    Terminal("terminator", "Terminator", ("terminator", "-T", "{title}", "-x")),
    Terminal("mate-terminal", "MATE Terminal", ("mate-terminal", "-t", "{title}", "-x")),
    Terminal("lxterminal", "LXTerminal", ("lxterminal", "-t", "{title}", "-e")),
    Terminal("xterm", "xterm", ("xterm", "-T", "{title}", "-e")),
)
_BY_ID = {t.id: t for t in KNOWN}
# macOS: the system's Terminal is always there; iTerm2 when installed; and the
# terminals above that run the same way on a Mac.
MAC_TERMINAL = Terminal("terminal-app", "Terminal", ("open", "-a", "Terminal"), opens_file=True)
ITERM = Terminal(
    "iterm2",
    "iTerm2",
    ("osascript",),
    via_applescript=True,
    failure_hint=(
        "If macOS asked to let this terminal control iTerm2 and it was refused, allow it in "
        "System Settings > Privacy & Security > Automation."
    ),
)
# The first time, osascript waits while macOS asks for Automation permission.
APPLESCRIPT_TIMEOUT = 120.0
_MAC_CLI = ("kitty", "alacritty", "wezterm")
# Names that resolve to a known terminal, e.g. Debian's x-terminal-emulator alternative.
_ALIASES = {"gnome-terminal.wrapper": "gnome-terminal", "konsole5": "konsole"}


@dataclass(frozen=True)
class Terminals:
    default: Terminal | None
    installed: tuple[Terminal, ...]


def detect_terminals(host: Host, os_name: str | None = None) -> Terminals:
    if (os_name or platform.system().lower()) == "darwin":
        return _detect_mac(host)
    installed = tuple(t for t in KNOWN if host.which(t.argv[0]))
    return Terminals(default=_default(host, installed), installed=installed)


def _detect_mac(host: Host) -> Terminals:
    home = host.env.get("HOME", "")
    installed = [MAC_TERMINAL]
    if host.exists("/Applications/iTerm.app") or (
        home and host.exists(f"{home}/Applications/iTerm.app")
    ):
        installed.append(ITERM)
    installed += [t for t in KNOWN if t.id in _MAC_CLI and host.which(t.argv[0])]
    return Terminals(default=MAC_TERMINAL, installed=tuple(installed))


def _default(host: Host, installed: tuple[Terminal, ...]) -> Terminal | None:
    env = host.env
    chosen = env.get("TERMINAL")
    if chosen:
        found = _known(os.path.basename(chosen.split()[0]))
        if found and host.which(found.argv[0]):
            return found
        if host.which(chosen.split()[0]):
            return Terminal("custom", chosen, (*shlex.split(chosen), "-e"))
    if host.which("xdg-terminal-exec"):
        return Terminal("xdg-terminal-exec", "default terminal", ("xdg-terminal-exec",))
    desktop = (env.get("XDG_CURRENT_DESKTOP") or "").upper()
    setting = None
    if "GNOME" in desktop or "UNITY" in desktop or "CINNAMON" in desktop:
        result = host.run(
            ["gsettings", "get", "org.gnome.desktop.default-applications.terminal", "exec"]
        )
        if result is not None and result.ok:
            setting = result.stdout.strip().strip("'\"")
    elif "KDE" in desktop:
        for tool in ("kreadconfig6", "kreadconfig5"):
            result = host.run([tool, "--group", "General", "--key", "TerminalApplication"])
            if result is not None and result.ok and result.stdout.strip():
                setting = result.stdout.strip()
                break
        setting = setting or "konsole"
    if setting and setting != "x-terminal-emulator":
        found = _known(os.path.basename(setting.split()[0]))
        if found and host.which(found.argv[0]):
            return found
    alternative = host.which("x-terminal-emulator")
    if alternative:
        found = _known(os.path.basename(host.realpath(alternative)))
        if found and host.which(found.argv[0]):
            return found
    return installed[0] if installed else None


def _known(name: str) -> Terminal | None:
    return _BY_ID.get(_ALIASES.get(name, name))


def by_id(terminal_id: str, terminals: Terminals) -> Terminal | None:
    if terminals.default is not None and terminals.default.id == terminal_id:
        return terminals.default
    return next((t for t in terminals.installed if t.id == terminal_id), None)


def current_terminal(env: Mapping[str, str]) -> str | None:
    """The terminal this process runs in, from the variables terminals set."""
    if env.get("KONSOLE_VERSION"):
        return "konsole"
    if env.get("KITTY_WINDOW_ID"):
        return "kitty"
    if env.get("ALACRITTY_SOCKET") or env.get("ALACRITTY_LOG"):
        return "alacritty"
    if env.get("WEZTERM_PANE"):
        return "wezterm"
    if env.get("GNOME_TERMINAL_SCREEN") or env.get("GNOME_TERMINAL_SERVICE"):
        return "gnome-terminal"
    if env.get("TILIX_ID"):
        return "tilix"
    if env.get("TERMINATOR_UUID"):
        return "terminator"
    program = env.get("TERM_PROGRAM")  # set by the macOS terminals
    if program == "Apple_Terminal":
        return MAC_TERMINAL.id
    if program == "iTerm.app":
        return ITERM.id
    if env.get("VTE_VERSION"):
        return "vte"  # one of several VTE-based terminals; which one is not known
    return None


def runs_in(terminal_id: str, env: Mapping[str, str]) -> bool:
    """Whether this process already runs in the chosen terminal (then no new window opens)."""
    current = current_terminal(env)
    if current == terminal_id:
        return True
    vte = {"gnome-terminal", "ptyxis", "kgx", "xfce4-terminal", "mate-terminal", "lxterminal"}
    return current == "vte" and terminal_id in vte


def open_in_terminal(host: Host, terminal: Terminal, command: Sequence[str], title: str) -> bool:
    if terminal.via_applescript:
        script = write_command_script(command, title)
        result = host.run(["osascript", "-e", iterm_script(script)], timeout=APPLESCRIPT_TIMEOUT)
        if result is not None and result.ok:
            return True
        script.unlink(missing_ok=True)  # never ran, so it did not delete itself
        return False
    if terminal.opens_file:
        return host.spawn([*terminal.argv, str(write_command_script(command, title))])
    return host.spawn(terminal.command(command, title))


def command_script(command: Sequence[str], title: str) -> str:
    """The script a macOS terminal app opens: remove itself, set the title, run the command."""
    return (
        "#!/bin/sh\n"
        'rm -f "$0"\n'
        f"printf '\\033]0;%s\\007' {shlex.quote(title)}\n"
        f"exec {shlex.join(command)}\n"
    )


def iterm_script(script: Path) -> str:
    """AppleScript that types ``script; exit`` into a new iTerm2 window's shell.

    This is what iTerm2 does with a file it is asked to open, minus its "OK to run" prompt.
    """
    line = f"{shlex.quote(str(script))}; exit"
    quoted = line.replace("\\", "\\\\").replace('"', '\\"')
    return (
        'tell application "iTerm"\n'
        "    set w to (create window with default profile)\n"
        f'    tell current session of w to write text "{quoted}"\n'
        "end tell"
    )


def write_command_script(command: Sequence[str], title: str) -> Path:
    handle, name = tempfile.mkstemp(prefix="ornith-launcher-", suffix=".command")
    with os.fdopen(handle, "w", encoding="utf-8") as script:
        script.write(command_script(command, title))
    os.chmod(name, 0o700)
    return Path(name)
