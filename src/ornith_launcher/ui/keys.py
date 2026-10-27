"""Reading single key presses: termios on Linux and macOS, msvcrt on Windows (no curses)."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

_ESCAPES = {
    "[A": "up",
    "[B": "down",
    "[C": "right",
    "[D": "left",
    "[5~": "pgup",
    "[6~": "pgdn",
    "[H": "home",
    "[F": "end",
    "OA": "up",
    "OB": "down",
}
_WINDOWS_KEYS = {"H": "up", "P": "down", "K": "left", "M": "right", "I": "pgup", "Q": "pgdn"}


@contextmanager
def raw_keys() -> Iterator[None]:
    """Put the terminal into raw mode for the duration (POSIX); a no-op on Windows."""
    if os.name == "nt":
        yield
        return
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def read_key(timeout: float | None = None) -> str:
    """One key: a character, or a name such as "up", "enter", "tab", "esc", "backspace".

    With ``timeout`` (seconds), returns "" when no key comes in time.
    """
    if os.name == "nt":
        import msvcrt

        if timeout is not None:
            deadline = time.monotonic() + timeout
            while not msvcrt.kbhit():
                if time.monotonic() >= deadline:
                    return ""
                time.sleep(0.02)
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            return _WINDOWS_KEYS.get(msvcrt.getwch(), "")
        return _name(char)
    import select

    if timeout is not None and not select.select([sys.stdin], [], [], timeout)[0]:
        return ""
    fd = sys.stdin.fileno()
    raw = os.read(fd, 1)
    # A character outside ASCII arrives as several bytes; read them all before decoding.
    for _ in range(utf8_length(raw[0]) - 1 if raw else 0):
        raw += os.read(fd, 1)
    char = raw.decode(errors="replace")
    if char != "\x1b":
        return _name(char)
    sequence = ""
    while select.select([sys.stdin], [], [], 0.03)[0]:
        sequence += os.read(sys.stdin.fileno(), 1).decode(errors="replace")
        if sequence in _ESCAPES:
            return _ESCAPES[sequence]
        if len(sequence) > 4:
            break
    return "esc" if not sequence else ""


def utf8_length(lead: int) -> int:
    """How many bytes the UTF-8 character starting with byte ``lead`` takes (1 to 4)."""
    if lead >= 0xF0:
        return 4
    if lead >= 0xE0:
        return 3
    if lead >= 0xC0:
        return 2
    return 1


def _name(char: str) -> str:
    if char in ("\r", "\n"):
        return "enter"
    if char == "\t":
        return "tab"
    if char in ("\x7f", "\x08"):
        return "backspace"
    if char == "\x03":
        raise KeyboardInterrupt
    if char == "\x04":
        return "eof"
    return char
