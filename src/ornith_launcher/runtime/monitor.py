"""The server monitor: a status line every 15 seconds, and a clean stop on Ctrl+C.

Ctrl+C asks "Stop the server and free its memory? [Y/n]". The answer defaults
to yes, a second Ctrl+C counts as yes, and no answer within 30 seconds counts
as yes, so a half-attended window never leaves a server holding memory.
"""

from __future__ import annotations

import os
import select
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TextIO

from ornith_launcher.profiles import Profile
from ornith_launcher.runtime.backend import Backend
from ornith_launcher.system.host import Host
from ornith_launcher.system.memory import parse_meminfo
from ornith_launcher.system.thermal import read_thermal

INTERVAL = 15.0
ANSWER_TIMEOUT = 30.0
WARN_MARGIN = 10.0  # degrees C before throttling
WARN_SWAP = 2 * 1024**3
GIB = 1024**3


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
    memory = parse_meminfo(host.read_text("/proc/meminfo") or "")
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


def ask_to_stop(
    out: TextIO,
    read_answer: Callable[[float], str | None],
) -> bool:
    """Ask whether to stop the server; True unless the user answers no in time."""
    out.write("\nStop the server and free its memory? [Y/n] ")
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

    def run(self, max_lines: int | None = None) -> bool:
        """Print status lines until Ctrl+C; returns True if the server was stopped."""
        self.say(
            f"Monitoring {self.profile.name} every {self.interval:.0f} s. "
            "Ctrl+C asks whether to stop the server (default yes)."
        )
        printed = 0
        try:
            while max_lines is None or printed < max_lines:
                line, warnings = status_line(self.host, self.backend, self.profile, self.now())
                self.say(line)
                for warning in warnings:
                    self.say(f"  Warning: {warning}")
                printed += 1
                if self.backend.status(self.profile).state == "missing":
                    self.say("The server is gone (stopped elsewhere). Closing the monitor.")
                    return False
                self.sleep(self.interval)
        except KeyboardInterrupt:
            return self.interrupted()
        return False

    def interrupted(self) -> bool:
        if ask_to_stop(self.out, self.read_answer):
            self.backend.stop(self.profile)
            self.say("Server stopped; its memory is free again.")
            return True
        self.say(
            f"The server keeps running. Stop it with: ornith-launcher stop {self.profile.name}"
        )
        return False

    def say(self, text: str) -> None:
        print(text, file=self.out, flush=True)
