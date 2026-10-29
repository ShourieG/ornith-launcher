"""The server monitor: the server's log as it comes, a status line every 15 seconds,
and a clean stop on Ctrl+C.

Ctrl+C asks "Stop the server and free its memory? [Y/n] (default: yes)". The answer defaults
to yes, a second Ctrl+C counts as yes, and no answer within 30 seconds counts
as yes, so a half-attended window never leaves a server holding memory.

The launcher's own polling (health, metrics) is left out of the log lines. With a
chat log window (``chat_log``), c and Enter opens that window again.
"""

from __future__ import annotations

import os
import re
import select
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TextIO

from ornith_launcher.profiles import Profile
from ornith_launcher.runtime.backend import Backend
from ornith_launcher.system.host import Host, LineStream
from ornith_launcher.system.memory import read_meminfo
from ornith_launcher.system.thermal import read_thermal
from ornith_launcher.ui.prompts import YES_HINT

INTERVAL = 15.0
ANSWER_TIMEOUT = 30.0
WARN_MARGIN = 10.0  # degrees C before throttling
WARN_SWAP = 2 * 1024**3
GIB = 1024**3
STATUS_MARK = "--"  # starts each status line, to set it apart from the server's own lines
# The launcher's own requests, which would otherwise show every 15 seconds.
_OWN_REQUESTS = re.compile(r"request: (GET|HEAD) /(v1/)?(health|metrics|props|slots|models)\b")


def status_line(host: Host, backend: Backend, profile: Profile, now: str) -> tuple[str, list[str]]:
    """One status line and any warnings."""
    warnings = []
    parts = [now]
    thermal = read_thermal(host)
    if thermal is not None and thermal.temperature is not None:
        gpu = f"GPU {thermal.temperature:.0f}°C"
        if thermal.margin is not None:
            gpu += f" (throttle margin {thermal.margin:.0f}°C)"
            if thermal.margin < WARN_MARGIN:
                warnings.append(
                    f"The GPU is {thermal.margin:.0f}°C from throttling: check the vents and "
                    "airflow, or run fewer requests at once."
                )
        if thermal.power is not None:
            gpu += f"  {thermal.power:.0f} W"
        if thermal.utilisation is not None:
            gpu += f"  load {thermal.utilisation:.0f}%"
        parts.append(gpu)
    memory = read_meminfo(host)
    if "MemAvailable" in memory:
        parts.append(f"memory free {memory['MemAvailable'] / GIB:.0f} GiB")
    if "SwapTotal" in memory:
        swap = memory["SwapTotal"] - memory.get("SwapFree", 0)
        parts.append(f"swap {swap / GIB:.1f} GiB")
        if swap > WARN_SWAP:
            warnings.append(
                f"{swap / GIB:.1f} GiB is in swap: answers slow down sharply once the server is "
                "paged out. Close other apps, or pick a smaller context or quant."
            )
    busy = backend.requests_in_progress(profile)
    parts.append(f"requests {busy if busy is not None else '-'}")
    parts.append(f"[{backend.status(profile).state}]")
    return "  ".join(parts), warnings


def stop_banner(profile_name: str, chat_log: bool = False) -> list[str]:
    """The lines at the top of the server window: how to stop it, and what closing it does.

    With a chat log window, also what that window's Ctrl+C does and how to bring it back.
    """
    rule = "=" * 72
    lines = [
        rule,
        f"  Ornith model server: {profile_name}",
        "  Press Ctrl+C here to stop the server and unload the model from memory.",
        "  Closing this window leaves the server running; stop it later with:",
        f"    ornith-launcher stop {profile_name}",
        f"  The server's log shows below, with a status line ({STATUS_MARK}) every 15 s.",
    ]
    if chat_log:
        lines += [
            "",
            "  Requests and replies show in the chat log window. Ctrl+C there, or",
            "  closing it, closes only that window, never the server. It closes by",
            "  itself when the server stops.",
            "  Chat log window closed or lost? Press c and Enter here. A new one opens",
            "  in its place (an old one still open is closed) and shows the recent",
            "  requests again.",
        ]
    return [*lines, rule]


def ask_to_stop(
    out: TextIO,
    read_answer: Callable[[float], str | None],
) -> bool:
    """Ask whether to stop the server; True unless the user answers no in time."""
    out.write(f"\nStop the server and free its memory? {YES_HINT} ")
    out.flush()
    try:
        answer = read_answer(ANSWER_TIMEOUT)
    except KeyboardInterrupt:
        out.write("\n")
        return True  # a second Ctrl+C means yes
    if answer is None:
        out.write("\n(no answer in 30 s: stopping)\n")
        return True
    return answer.strip().lower() not in ("n", "no")


def read_line_with_timeout(timeout: float) -> str | None:
    """A line from stdin, or None after ``timeout`` seconds (or at end of input)."""
    if os.name == "nt":
        import msvcrt

        deadline = time.monotonic() + timeout
        chars = []
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                char = msvcrt.getwche()
                if char in "\r\n":
                    return "".join(chars)
                chars.append(char)
            time.sleep(0.05)
        return None
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    line = sys.stdin.readline()
    return line if line else None


@dataclass
class Monitor:
    host: Host
    backend: Backend
    profile: Profile
    out: TextIO = field(default_factory=lambda: sys.stdout)
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], str] = lambda: time.strftime("%H:%M:%S")
    read_answer: Callable[[float], str | None] = read_line_with_timeout
    interval: float = INTERVAL
    reopen_chat: Callable[[], object] | None = None  # set when c and Enter reopen the chat log
    on_tick: Callable[[], object] | None = None  # after each status line
    on_stop: Callable[[], object] | None = None  # once the server has stopped
    clock: Callable[[], float] = time.monotonic
    show_log: bool = True  # the server's own log lines, between the status lines
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _held: deque = field(default_factory=lambda: deque(maxlen=500), init=False, repr=False)
    _paused: bool = field(default=False, init=False, repr=False)
    _stream: LineStream | None = field(default=None, init=False, repr=False)
    _reader: threading.Thread | None = field(default=None, init=False, repr=False)

    def run(self, max_lines: int | None = None) -> bool:
        """Print the log and status lines until Ctrl+C; returns True if the server was stopped."""
        self.say("")
        keys = "; c and Enter reopens the chat log window" if self.reopen_chat else ""
        self.say(
            f"Monitoring {self.profile.name}: a status line every {self.interval:.0f} s. "
            f"Ctrl+C asks whether to stop the server (default yes){keys}."
        )
        self._start_log()
        printed = 0
        try:
            while max_lines is None or printed < max_lines:
                line, warnings = status_line(self.host, self.backend, self.profile, self.now())
                self.say(f"{STATUS_MARK} {line}")
                for warning in warnings:
                    self.say(f"{STATUS_MARK} Warning: {warning}")
                printed += 1
                if self.backend.status(self.profile).state == "missing":
                    self.say("The server is gone (stopped elsewhere). Closing the monitor.")
                    self._stopped()
                    return False
                if self.on_tick is not None:
                    self.on_tick()
                self.wait()
        except KeyboardInterrupt:
            return self.interrupted()
        finally:
            self._stop_log()
        return False

    def wait(self) -> None:
        """Wait one interval; with a chat log window, watch for c and Enter meanwhile."""
        if self.reopen_chat is None:
            self.sleep(self.interval)
            return
        deadline = self.clock() + self.interval
        while self.reopen_chat is not None and self.clock() < deadline:
            line = self.read_answer(deadline - self.clock())
            if line is None:
                if self.clock() < deadline - 0.5:
                    self.reopen_chat = None  # input closed (Ctrl+D): stop watching for keys
                    self.sleep(max(0.0, deadline - self.clock()))
                return
            if line.strip().lower() == "c":
                self.reopen_chat()

    def interrupted(self) -> bool:
        with self._lock:
            self._paused = True  # log lines wait while the question is on screen
        if ask_to_stop(self.out, self.read_answer):
            self.backend.stop(self.profile)
            self.say("Server stopped; its memory is free again.")
            self._stopped()
            return True
        with self._lock:
            self._paused = False
            for line in self._held:
                print(line, file=self.out, flush=True)
            self._held.clear()
        self.say(
            f"The server keeps running. Stop it with: ornith-launcher stop {self.profile.name}"
        )
        return False

    def say(self, text: str) -> None:
        with self._lock:
            print(text, file=self.out, flush=True)

    def _stopped(self) -> None:
        if self.on_stop is not None:
            self.on_stop()

    # The server's log ---------------------------------------------------------------------

    def _start_log(self) -> None:
        if not self.show_log:
            return
        self._stream = self.backend.log_stream(self.profile)
        if self._stream is not None:
            self._reader = threading.Thread(target=self._read_log, args=(self._stream,))
            self._reader.daemon = True
            self._reader.start()

    def _read_log(self, stream: LineStream) -> None:
        try:
            for line in stream.lines():
                if not line.strip() or _OWN_REQUESTS.search(line):
                    continue
                with self._lock:
                    if self._paused:
                        self._held.append(line)
                    else:
                        print(line, file=self.out, flush=True)
        except (OSError, ValueError):
            pass  # the log follower was closed

    def _stop_log(self) -> None:
        if self._stream is not None:
            self._stream.close()
        if self._reader is not None:
            self._reader.join(timeout=5)
        self._stream = self._reader = None
