"""Full-screen list picking with ANSI escape codes (no curses, which Windows Python lacks).

Only lists are drawn full-screen: up and down scroll, left and right turn pages,
Enter picks, Esc goes back, letters run the list's actions, q quits. Everything
else (questions, progress) prints normally through the plain prompter this class
extends.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ornith_launcher.ui.keys import raw_keys, read_key
from ornith_launcher.ui.prompts import (
    BACK,
    NEXT_PAGE,
    PREVIOUS_PAGE,
    Cancelled,
    Choice,
    PlainPrompter,
    key_help,
    with_nav_keys,
)

ARROWS = "↑/↓"
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


def click_hint(env: Mapping[str, str] | None = None, platform: str | None = None) -> str:
    """How to open a link here, or "" where links are not drawn.

    Terminals that do not know OSC 8 links ignore them, but the Linux console and
    dumb terminals may print them as text.
    """
    env = os.environ if env is None else env
    if env.get("TERM", "") in ("linux", "dumb"):
        return ""
    click = "Cmd+click" if (platform or sys.platform) == "darwin" else "Ctrl+click"
    return f"{click} the name or URL"


def hyperlink(text: str, url: str) -> str:
    """``text`` as an OSC 8 terminal link to ``url``."""
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


def _shown_hint(choices: Sequence[Choice], click: str) -> str:
    return click if click and any(c.link for c in choices) else ""


# Keys that also work as a navigation key in full-screen lists.
_ALTERNATE_KEYS = {"right": NEXT_PAGE, "left": PREVIOUS_PAGE, "esc": BACK}


def alternates(
    previous_page: bool, next_page: bool, back: bool = False, arrows: str = ARROWS
) -> dict[str, str]:
    """Navigation key -> the other key that does the same, as shown in the actions."""
    left, right = ("←", "→") if arrows == ARROWS else ("Left", "Right")
    shown = {}
    if next_page:
        shown[NEXT_PAGE] = right
    if previous_page:
        shown[PREVIOUS_PAGE] = left
    if back:
        shown[BACK] = "Esc"
    return shown


def footer(
    keys: Mapping[str, str],
    arrows: str = ARROWS,
    click: str = "",
    pages: Mapping[str, str] | None = None,
) -> list[str]:
    pages = pages or {}
    entries = [("scroll", arrows), ("choose", "Enter")]
    if click:
        entries.append(("open model card", click))
    for key, label in keys.items():
        entries.append((label, f"{key} or {pages[key]}" if key in pages else key))
    return key_help([*entries, ("quit", "q")])


def notice_lines(notice: str, width: int) -> list[str]:
    return textwrap.wrap(notice, max(20, width - 2)) if notice else []


def body_height(
    height: int,
    keys: Mapping[str, str],
    click: str = "",
    pages: Mapping[str, str] | None = None,
    notice: int = 0,
) -> int:
    """List rows that fit between the title and the actions; ``notice`` is its line count."""
    # Title, notice, blank, header, blank, then after the rows: blank, detail, the URL
    # when rows link, blank, "Actions", blank and one line per action.
    fixed = 9 + notice + (1 if click else 0) + len(footer(keys, click=click, pages=pages))
    return max(3, height - fixed)


def arrows_for(out: object) -> str:
    """The arrow symbols, or plain words where the terminal's encoding has no arrows."""
    try:
        ARROWS.encode(getattr(out, "encoding", None) or "utf-8")
    except (UnicodeEncodeError, LookupError):
        return "Up/Down"
    return ARROWS


def render_list(
    title: str,
    headers: Sequence[str],
    choices: Sequence[Choice],
    selected: int,
    top: int,
    keys: Mapping[str, str],
    width: int,
    height: int,
    arrows: str = ARROWS,
    click: str = "",
    pages: Mapping[str, str] | None = None,
    notice: str = "",
) -> list[str]:
    """The screen as lines (without colour codes on unselected rows). Pure, for testing.

    With ``click`` set, a row's first cell links to its ``Choice.link``, and the
    highlighted row's link is also printed as a plain URL. Konsole only follows
    links on the main screen, not on the alternate screen used here, but it
    finds plain URLs anywhere.
    """
    widths = [len(h) for h in headers]
    for choice in choices:
        for index, cell in enumerate(choice.cells):
            widths[index] = max(widths[index], len(cell))
    click = _shown_hint(choices, click)
    notes = notice_lines(notice, width)

    def line(cells: Sequence[str]) -> str:
        text = "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()
        return text[: width - 3] if len(text) > width - 3 else text

    def linked(text: str, choice: Choice) -> str:
        # Added after the text is cut to width, so the escape codes take no columns.
        if not (click and choice.link):
            return text
        end = min(len(choice.cells[0]), len(text))
        return hyperlink(text[:end], choice.link) + text[end:]

    # The header gets the same two-character indent as the "> " marker on the rows.
    lines = [f"{BOLD}{title[:width]}{RESET}", *(f"  {n}" for n in notes)]
    lines += ["", f"  {BOLD}{line(headers)}{RESET}", ""]
    rows = body_height(height, keys, click, pages, len(notes))
    for index in range(top, min(len(choices), top + rows)):
        choice = choices[index]
        text = line(choice.cells)
        if index == selected:
            lines.append(f"{REVERSE}> {linked(text.ljust(width - 3), choice)}{RESET}")
        elif not choice.enabled:
            lines.append(f"{DIM}  {linked(text, choice)}{RESET}")
        else:
            lines.append(f"  {linked(text, choice)}")
    current = choices[selected] if choices else None
    lines.append("")
    lines.append(current.detail[:width] if current else "")
    if click:
        lines.append(current.link if current else "")
    lines.append("")
    lines.append(f"  {BOLD}Actions{RESET}")
    lines.append("")
    actions = footer(keys, arrows, click, pages)
    lines.extend(f"{DIM}  {text[: width - 2]}{RESET}" for text in actions)
    return lines


@dataclass
class TuiPrompter(PlainPrompter):
    def new_screen(self, title: str) -> None:
        # Clears what is visible, not the scrollback, so earlier steps can still be scrolled to.
        self.out.write(CLEAR)
        self.say(self.bold(title))
        self.say()

    def bold(self, text: str) -> str:
        return f"{BOLD}{text}{RESET}"

    def choose(
        self,
        title: str,
        headers: Sequence[str],
        choices: Sequence[Choice],
        *,
        default: int = 0,
        keys: dict[str, str] | None = None,
        notice: str = "",
        previous_page: bool = False,
        next_page: bool = False,
        back: bool = False,
        main_menu: bool = False,
    ) -> int | str:
        nav = {
            "previous_page": previous_page,
            "next_page": next_page,
            "back": back,
            "main_menu": main_menu,
        }
        if self.assume_yes or not choices:
            return super().choose(
                title, headers, choices, default=default, keys=keys, notice=notice, **nav
            )
        keys = with_nav_keys(keys or {}, **nav)
        selected = default
        top = 0
        arrows = arrows_for(self.out)
        click = _shown_hint(choices, click_hint())
        pages = alternates(previous_page, next_page, back, arrows)
        self.out.write(ALT_SCREEN_ON)
        try:
            with raw_keys():
                while True:
                    size = shutil.get_terminal_size((100, 30))
                    notes = len(notice_lines(notice, size.columns))
                    body = body_height(size.lines, keys, click, pages, notes)
                    top = min(max(top, selected - body + 1), selected)
                    lines = render_list(
                        title,
                        headers,
                        choices,
                        selected,
                        top,
                        keys,
                        size.columns,
                        size.lines,
                        arrows,
                        click,
                        pages,
                        notice,
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
                    elif key in _ALTERNATE_KEYS and _ALTERNATE_KEYS[key] in pages:
                        return _ALTERNATE_KEYS[key]
                    elif key in ("q", "esc", "eof"):
                        raise Cancelled()
                    elif key in keys:
                        return key
        finally:
            self.out.write(ALT_SCREEN_OFF)
            self.out.flush()
