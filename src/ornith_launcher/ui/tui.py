"""Full-screen list picking with ANSI escape codes (no curses, which Windows Python lacks).

Only lists are drawn full-screen: arrow keys move, Enter picks, letters run the
list's actions, q quits. Everything else (questions, progress) prints normally
through the plain prompter this class extends.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ornith_launcher.ui.keys import raw_keys, read_key
from ornith_launcher.ui.prompts import Cancelled, Choice, PlainPrompter

ALT_SCREEN_ON = "\x1b[?1049h\x1b[?25l"
ALT_SCREEN_OFF = "\x1b[?25h\x1b[?1049l"
CLEAR = "\x1b[H\x1b[2J"
REVERSE = "\x1b[7m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
RESET = "\x1b[0m"


def supports_full_screen(
    stdin: object = None, stdout: object = None, env: Mapping[str, str] | None = None
) -> bool:
    """True when both ends are a terminal that understands ANSI codes."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    env = os.environ if env is None else env
    if env.get("TERM", "") in ("", "dumb") and os.name != "nt":
        return False
    return bool(
        getattr(stdin, "isatty", lambda: False)() and getattr(stdout, "isatty", lambda: False)()
    )


def render_list(
    title: str,
    headers: Sequence[str],
    choices: Sequence[Choice],
    selected: int,
    top: int,
    keys: Mapping[str, str],
    width: int,
    height: int,
) -> list[str]:
    """The screen as lines (without colour codes on unselected rows). Pure, for testing."""
    widths = [len(h) for h in headers]
    for choice in choices:
        for index, cell in enumerate(choice.cells):
            widths[index] = max(widths[index], len(cell))

    def line(cells: Sequence[str]) -> str:
        text = "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()
        return text[: width - 3] if len(text) > width - 3 else text

    body_height = max(3, height - 6)
    lines = [f"{BOLD}{title[:width]}{RESET}", "   " + line(headers)]
    for index in range(top, min(len(choices), top + body_height)):
        choice = choices[index]
        text = line(choice.cells)
        if index == selected:
            lines.append(f"{REVERSE}> {text.ljust(width - 3)}{RESET}")
        elif not choice.enabled:
            lines.append(f"{DIM}  {text}{RESET}")
        else:
            lines.append(f"  {text}")
    detail = choices[selected].detail if choices else ""
    lines.append("")
    lines.append(detail[:width])
    footer = ["Up/Down move", "Enter choose"] + [f"{k} {label}" for k, label in keys.items()]
    lines.append(DIM + "  ".join(footer + ["q quit"])[:width] + RESET)
    return lines


@dataclass
class TuiPrompter(PlainPrompter):
    def choose(
        self,
        title: str,
        headers: Sequence[str],
        choices: Sequence[Choice],
        *,
        default: int = 0,
        keys: dict[str, str] | None = None,
    ) -> int | str:
        keys = keys or {}
        if self.assume_yes or not choices:
            return super().choose(title, headers, choices, default=default, keys=keys)
        selected = default
        top = 0
        self.out.write(ALT_SCREEN_ON)
        try:
            with raw_keys():
                while True:
                    size = shutil.get_terminal_size((100, 30))
                    body = max(3, size.lines - 6)
                    top = min(max(top, selected - body + 1), selected)
                    lines = render_list(
                        title, headers, choices, selected, top, keys, size.columns, size.lines
                    )
                    self.out.write(CLEAR + "\r\n".join(lines))
                    self.out.flush()
                    key = read_key()
                    if key in ("up", "k"):
                        selected = max(0, selected - 1)
                    elif key in ("down", "j"):
                        selected = min(len(choices) - 1, selected + 1)
                    elif key == "pgup":
                        selected = max(0, selected - body)
                    elif key == "pgdn":
                        selected = min(len(choices) - 1, selected + body)
                    elif key == "enter" and choices[selected].enabled:
                        return selected
                    elif key in ("q", "esc", "eof"):
                        raise Cancelled()
                    elif key in keys:
                        return key
        finally:
            self.out.write(ALT_SCREEN_OFF)
            self.out.flush()
