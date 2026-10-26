"""The chat log window: each request sent to a model and its reply, live, next to the server.

Setup can turn it on for a profile (``terminal["chat_log"]``). A launch then runs
the chat log proxy (``runtime.chat_proxy``) as a background process on the
profile's port, in front of the server, and opens a window that shows what passes
through. Nothing is written to disk: the proxy keeps the last few requests in
memory, so a window reopened from the server window (c and Enter) shows them again.

The proxy ends by itself when the server stops, and the window closes with it.
Stopping the server from the launcher (Ctrl+C in the server window, ``stop``) also
closes both at once. Ctrl+C in the window, or closing it, closes only the window.

Both processes write a run file, so the launcher can find them again; a pid is only
acted on while its command line is still ours.
"""

from __future__ import annotations

import http.client
import json
import os
import secrets
import signal
import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ornith_launcher.app import App, self_command
from ornith_launcher.config import atomic_write_json, read_json
from ornith_launcher.integrations.terminals import (
    THIS_TERMINAL,
    Terminal,
    by_id,
    detect_terminals,
    open_in_terminal,
)
from ornith_launcher.profiles import Profile
from ornith_launcher.runtime.chat_capture import ChatView
from ornith_launcher.runtime.chat_proxy import KEEP_EXCHANGES, TOKEN_HEADER, VIEW_PATH, ChatProxy
from ornith_launcher.runtime.docker import DockerRuntime

PROXY_COMMAND = "chat-proxy"  # the hidden command the proxy process runs
IN_WINDOW = "--in-window"  # the flag the window's own process runs with
WAIT_SECONDS = 30  # how long a window waits for a server that is still starting
READY_SECONDS = 10.0  # how long a launch waits for the proxy to listen
WATCH_SECONDS = 2.0  # how often the proxy checks that the server still runs


def enabled(profile: Profile) -> bool:
    return bool(profile.terminal.get("chat_log"))


def banner(profile_name: str) -> list[str]:
    """The lines at the top of the chat log window."""
    rule = "=" * 72
    return [
        rule,
        f"  Chat log: {profile_name}",
        "  Each request sent to the model and its reply, as it streams. Nothing is",
        f"  saved to disk: the last {KEEP_EXCHANGES} requests are kept in memory only, until",
        "  the server stops.",
        "  Ctrl+C here, or closing this window, closes only the chat log. The server",
        "  keeps running, and this window closes by itself when the server stops.",
        "  Reopen it from the server window with c and Enter, or run:",
        f"    ornith-launcher chat-log {profile_name}",
        rule,
    ]


# Run files ------------------------------------------------------------------------------


def window_file(app: App, profile_name: str) -> Path:
    return app.paths.state / "run" / f"chat-window-{profile_name}.pid"


def proxy_file(app: App, profile_name: str) -> Path:
    return app.paths.state / "run" / f"chat-proxy-{profile_name}.json"


def proxy_error_file(app: App, profile_name: str) -> Path:
    return app.paths.state / "run" / f"chat-proxy-{profile_name}.error"


def _ours(app: App, pid: int, command: str, profile_name: str) -> bool:
    # A stale file may name a pid that now belongs to another program: never touch that.
    words = (app.host.process_command(pid) or "").split()
    return command in words and profile_name in words


def window_pid(app: App, profile_name: str) -> int | None:
    try:
        pid = int(window_file(app, profile_name).read_text().strip())
    except (OSError, ValueError):
        return None
    ours = _ours(app, pid, "chat-log", profile_name)
    return pid if ours and IN_WINDOW in (app.host.process_command(pid) or "") else None


def proxy_info(app: App, profile_name: str) -> dict[str, Any] | None:
    """The running proxy's pid, port and token, or None when it does not run."""
    try:
        info = read_json(proxy_file(app, profile_name)) or {}
        pid = int(info["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return info if _ours(app, pid, PROXY_COMMAND, profile_name) else None


# The proxy ------------------------------------------------------------------------------


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def start_proxy(
    app: App,
    profile: Profile,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    """Start the proxy unless it runs; None once it listens, else what went wrong."""
    if proxy_info(app, profile.name) is not None:
        return None
    errors = proxy_error_file(app, profile.name)
    errors.unlink(missing_ok=True)
    if not app.host.spawn([*self_command(), PROXY_COMMAND, profile.name]):
        return "the chat log proxy could not be started"
    deadline = clock() + READY_SECONDS
    while clock() < deadline:
        if proxy_info(app, profile.name) is not None:
            return None
        if errors.exists():
            return errors.read_text(encoding="utf-8").strip()
        sleep(0.1)
    return f"the chat log proxy did not start within {READY_SECONDS:.0f} s"


def shutdown(app: App, profile_name: str) -> None:
    """End the chat log proxy and close its window, if they run."""
    info = proxy_info(app, profile_name)
    if info is not None:
        app.host.terminate(int(info["pid"]))
    pid = window_pid(app, profile_name)
    if pid is not None:
        app.host.terminate(pid)


def run_proxy(app: App, runtime: DockerRuntime, profile: Profile) -> int:
    """The proxy process: forward the profile's port to the server until it stops."""
    proxy = ChatProxy(
        upstream=lambda: runtime.host_port(profile, cached=False), token=secrets.token_urlsafe(24)
    )
    try:
        proxy.bind(profile.runtime.port)
    except OSError as exc:
        proxy_error_file(app, profile.name).parent.mkdir(parents=True, exist_ok=True)
        proxy_error_file(app, profile.name).write_text(
            f"port {profile.runtime.port} is in use by another program ({exc.strerror})",
            encoding="utf-8",
        )
        return 1
    path = proxy_file(app, profile.name)
    atomic_write_json(path, {"pid": os.getpid(), "port": proxy.port, "token": proxy.token}, 0o600)
    previous = signal.signal(signal.SIGTERM, _raise_interrupt)
    server = threading.Thread(target=proxy.serve, daemon=True)
    server.start()
    try:
        _watch(runtime, profile)
    except KeyboardInterrupt:
        pass
    finally:
        proxy.shutdown()
        signal.signal(signal.SIGTERM, previous)
        _remove_if_ours(path, lambda text: json.loads(text).get("pid"))
    return 0


def _watch(runtime: DockerRuntime, profile: Profile, sleep=time.sleep) -> None:
    """Return once the server has stopped (two checks in a row, to ride out a restart)."""
    gone = 0
    while gone < 2:
        sleep(WATCH_SECONDS)
        gone = 0 if runtime.status(profile).alive else gone + 1


# The window -----------------------------------------------------------------------------


def log_terminal(app: App, profile: Profile) -> Terminal | None:
    """The server window's terminal app, else the system default one."""
    terminals = detect_terminals(app.host)
    terminal_id = profile.terminal.get("id")
    if terminal_id and terminal_id != THIS_TERMINAL:
        found = by_id(terminal_id, terminals)
        if found is not None:
            return found
    if terminals.default is not None:
        return terminals.default
    return terminals.installed[0] if terminals.installed else None


def open_window(app: App, profile: Profile) -> bool:
    """Open the chat log window, replacing one that is still open; False if it could not."""
    fallback = f"See the chat log in any terminal with: ornith-launcher chat-log {profile.name}"
    terminal = log_terminal(app, profile)
    if terminal is None:
        app.say(f"No terminal app was found for the chat log window. {fallback}")
        return False
    old = window_pid(app, profile.name)
    if old is not None:
        app.host.terminate(old)
    command = [*self_command(), "chat-log", profile.name, IN_WINDOW]
    if not open_in_terminal(app.host, terminal, command, f"Ornith chat log: {profile.name}"):
        app.say(f"Could not open {terminal.name} for the chat log window. {fallback}")
        return False
    if old is not None:
        app.say("Opened a new chat log window in place of the old one.")
    else:
        app.say(f"Opened the chat log in a separate {terminal.name} window.")
    return True


def follow(
    app: App,
    runtime: DockerRuntime,
    profile: Profile,
    in_window: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    connect: Callable[[dict[str, Any]], Iterator[dict[str, Any]]] | None = None,
) -> int:
    """Show the chat log until the server stops or Ctrl+C; the server is never stopped."""
    previous = None
    if in_window:
        for line in banner(profile.name):
            app.say(line)
        previous = _claim_window(app, profile.name)
    try:
        return _follow(app, runtime, profile, in_window, sleep, connect or _events)
    except KeyboardInterrupt:
        app.say()
        app.say("Closed the chat log. The server keeps running.")
        return 0
    finally:
        if in_window:
            _release_window(app, profile.name, previous)


def _follow(
    app: App,
    runtime: DockerRuntime,
    profile: Profile,
    in_window: bool,
    sleep: Callable[[float], None],
    connect: Callable[[dict[str, Any]], Iterator[dict[str, Any]]],
) -> int:
    view = ChatView()
    while True:
        info = _wait_for_proxy(app, runtime, profile, sleep)
        if info is None:
            _wait_to_close(app, in_window)
            return 1
        try:
            for event in connect(info):
                app.out.write(view.feed(event))
                app.out.flush()
        except (OSError, ValueError, http.client.HTTPException):
            pass
        if not runtime.status(profile).alive:
            break
        app.say()
        app.say("(The chat log proxy stopped; waiting for it to start again.)")
    app.say()
    app.say(f"The server {profile.name} stopped.")
    return 0


def _wait_for_proxy(
    app: App, runtime: DockerRuntime, profile: Profile, sleep: Callable[[float], None]
) -> dict[str, Any] | None:
    """The proxy's run file once it listens; None (and why) when it will not come."""
    waited = 0
    while (info := proxy_info(app, profile.name)) is None:
        if runtime.status(profile).alive and runtime.host_port(profile) == profile.runtime.port:
            app.say(
                f"{profile.name} runs without the chat log. To turn it on, stop it and start it "
                f"again with: ornith-launcher launch {profile.name} --chat-log"
            )
            return None
        if waited >= WAIT_SECONDS:
            app.say(
                f"{profile.name} is not running. Start it with: ornith-launcher launch "
                f"{profile.name}"
            )
            return None
        if waited == 0:
            app.say(f"Waiting for the chat log of {profile.name}...")
        sleep(1)
        waited += 1
    return info


def _events(info: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """The proxy's events, as they happen; ends when the proxy does."""
    connection = http.client.HTTPConnection("127.0.0.1", int(info["port"]), timeout=None)
    try:
        connection.request("GET", VIEW_PATH, headers={TOKEN_HEADER: str(info["token"])})
        response = connection.getresponse()
        if response.status != 200:
            return
        for line in response:
            if line.strip():
                yield json.loads(line)
    finally:
        connection.close()


def _wait_to_close(app: App, in_window: bool) -> None:
    """Keep a window open until it is read; in the user's own terminal, just end."""
    if not in_window:
        return
    try:
        app.read_line("Press Enter to close this window. ")
    except EOFError:
        pass


def _claim_window(app: App, profile_name: str) -> Any:
    """Record this window's pid; returns the SIGTERM handler it replaced."""
    path = window_file(app, profile_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n")
    # Being replaced or shut down sends SIGTERM: end like Ctrl+C.
    return signal.signal(signal.SIGTERM, _raise_interrupt)


def _release_window(app: App, profile_name: str, previous: Any) -> None:
    signal.signal(signal.SIGTERM, previous)
    _remove_if_ours(window_file(app, profile_name), lambda text: text.strip())


def _remove_if_ours(path: Path, pid_of: Callable[[str], Any]) -> None:
    try:
        if str(pid_of(path.read_text())) == str(os.getpid()):
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def _raise_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt
