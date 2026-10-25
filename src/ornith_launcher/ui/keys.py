"""Reading single key presses: termios on Linux and macOS, msvcrt on Windows (no curses)."""

from __future__ import annotations

import os
import sys
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


def read_key() -> str:
    """One key: a character, or a name such as "up", "enter", "tab", "esc", "backspace"."""
    if os.name == "nt":
        import msvcrt

        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            return _WINDOWS_KEYS.get(msvcrt.getwch(), "")
        return _name(char)
    char = os.read(sys.stdin.fileno(), 1).decode(errors="replace")
    if char != "\x1b":
        return _name(char)
    import select

    sequence = ""
    while select.select([sys.stdin], [], [], 0.03)[0]:
        sequence += os.read(sys.stdin.fileno(), 1).decode(errors="replace")
        if sequence in _ESCAPES:
            return _ESCAPES[sequence]
        if len(sequence) > 4:
            break
    return "esc" if not sequence else ""


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
