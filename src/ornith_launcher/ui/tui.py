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
from ornith_launcher.ui.plain import clean_cell, clip, pad, table_line, text_width
from ornith_launcher.ui.prompts import (
    NEXT_PAGE,
    PREVIOUS_PAGE,
    Cancelled,
    Choice,
    PlainPrompter,
    detail_lines,
    key_help,
    list_widths,
    shown_key,
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
_ALTERNATE_KEYS = {"right": NEXT_PAGE, "left": PREVIOUS_PAGE}


def alternates(previous_page: bool, next_page: bool, arrows: str = ARROWS) -> dict[str, str]:
    """Navigation key -> the other key that does the same, as shown in the actions."""
    left, right = ("←", "→") if arrows == ARROWS else ("Left", "Right")
    shown = {}
    if next_page:
        shown[NEXT_PAGE] = right
    if previous_page:
        shown[PREVIOUS_PAGE] = left
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
        name = shown_key(key)
        entries.append((label, f"{name} or {pages[key]}" if key in pages else name))
    return key_help([*entries, ("quit", "q")])


def notice_lines(notice: str, width: int) -> list[str]:
    """The notice wrapped to ``width``, keeping its own line breaks."""
    if not notice:
        return []
    rows = [row for line in notice.split("\n") for row in textwrap.wrap(line, max(20, width - 2))]
    return [clip(row, width - 2) for row in rows]  # a long word is cut, never wrapped


def detail_height(choices: Sequence[Choice], width: int) -> int:
    """Lines the detail area takes: the tallest of the choices, so the list never jumps."""
    return max((len(detail_lines(c, width - 2, str)) for c in choices), default=0) or 1


def body_height(
    height: int,
    keys: Mapping[str, str],
    click: str = "",
    pages: Mapping[str, str] | None = None,
    notice: int = 0,
    detail: int = 1,
    gaps: int = 0,
) -> int:
    """List rows that fit between the title and the actions.

    ``notice`` and ``detail`` are the line counts of the notice and the detail area, and
    ``gaps`` the blank lines before action rows.
    """
    # Title, notice, blank, header, blank, then after the rows: blank, the detail area
    # (notes and the URL), blank, "Actions", blank and one line per action.
    fixed = 8 + notice + detail + gaps + len(footer(keys, click=click, pages=pages))
    return max(3, height - fixed)


def arrows_for(out: object) -> str:
    """The arrow symbols, or plain words where the terminal's encoding has no arrows."""
    try:
        ARROWS.encode(getattr(out, "encoding", None) or "utf-8")
    except (UnicodeEncodeError, LookupError):
        return "Up/Down"
    return ARROWS


def _tail(text: str, room: int) -> str:
    """The end of ``text`` that fits in ``room`` columns, after "…" when the start is cut."""
    if text_width(text) <= room:
        return text
    kept = ""
    for ch in reversed(text):
        if text_width(ch + kept) > max(1, room - 1):
            break
        kept = ch + kept
    return "…" + kept


def _gaps(choices: Sequence[Choice]) -> int:
    return sum(1 for c in choices if c.action)


def _bold(text: str) -> str:
    return f"{BOLD}{text}{RESET}"


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
    # Header and rows share one set of widths, counted in terminal columns, and every line
    # is cut (never wrapped) at the edge, so a long or wide cell cannot shift a column.
    headers = [clean_cell(h) for h in headers]
    cells_of = [[clean_cell(cell) for cell in choice.cells] for choice in choices]
    widths = list_widths(headers, choices)
    click = _shown_hint(choices, click)
    notes = notice_lines(notice, width)

    def line(cells: Sequence[str]) -> str:
        return clip(table_line(cells, widths), width - 3)

    def linked(text: str, index: int) -> str:
        # Added after the text is cut to width, so the escape codes take no columns.
        choice = choices[index]
        if not (click and choice.link):
            return text
        first = cells_of[index][0]
        end = len(first) if text.startswith(first) else len(text)
        return hyperlink(text[:end], choice.link) + text[end:]

    # The header gets the same two-character indent as the "> " marker on the rows.
    lines = [f"{BOLD}{clip(clean_cell(title), width)}{RESET}", *(f"  {n}" for n in notes)]
    lines += ["", f"  {BOLD}{line(headers)}{RESET}", ""]
    tallest = detail_height(choices, width)
    rows = body_height(height, keys, click, pages, len(notes), tallest, _gaps(choices))
    for index in range(top, min(len(choices), top + rows)):
        choice = choices[index]
        text = line(cells_of[index])
        if choice.action:
            lines.append("")  # an action stands apart from the rows of data
            text = clip(cells_of[index][0], width - 3)
            marker = f"{REVERSE}> " if index == selected else "  "
            lines.append(
                f"{marker}{BOLD}{pad(text, width - 3) if index == selected else text}{RESET}"
            )
        elif index == selected:
            lines.append(f"{REVERSE}> {linked(pad(text, width - 3), index)}{RESET}")
        elif not choice.enabled:
            lines.append(f"{DIM}  {linked(text, index)}{RESET}")
        else:
            lines.append(f"  {linked(text, index)}")
    current = choices[selected] if choices else None
    lines.append("")
    details = detail_lines(current, width - 2, _bold) if current else []
    url = details.pop() if current and current.link else None
    # Indented like the rows. The notes are wrapped to fit already (clip() would count the
    # bold codes as columns), and the URL line is never cut, so the terminal can find it.
    lines.extend(f"  {d}" if d else "" for d in details)
    if url is not None:
        lines.append(f"  {url}")
    lines.extend([""] * (tallest - len(details) - (url is not None)))
    lines.append("")
    lines.append(f"  {BOLD}Actions{RESET}")
    lines.append("")
    actions = footer(keys, arrows, click, pages)
    # Plain, not dim: dim marks rows that cannot be chosen, and every action here works.
    lines.extend(f"  {clip(text, width - 2)}" for text in actions)
    return lines


# How often a waiting list checks for a resized window, in seconds.
RESIZE_POLL = 0.25


def _next_key(size: os.terminal_size) -> str:
    """The next key press, or "" once the window no longer has ``size``."""
    while True:
        key = read_key(timeout=RESIZE_POLL)
        if key or shutil.get_terminal_size((100, 30)) != size:
            return key


@dataclass
class TuiPrompter(PlainPrompter):
    def new_screen(self, title: str) -> None:
        # Clears what is visible, not the scrollback, so earlier steps can still be scrolled to.
        self.out.write(CLEAR)
        self.say(self.bold(title))
        self.say()

    def bold(self, text: str) -> str:
        return f"{BOLD}{text}{RESET}"

    def _menu_key(self, keys: Mapping[str, str]) -> str:
        """One of ``keys``, pressed without Enter; q, Ctrl+D or an unused Esc quits."""
        self.out.write("\nPress a key: ")
        self.out.flush()
        with raw_keys():
            while True:
                key = read_key()
                key = key.lower() if len(key) == 1 else key
                if key in keys:
                    self.out.write(f"{shown_key(key)}\n")
                    return key
                if key in ("q", "esc", "eof"):
                    self.out.write("\n")
                    raise Cancelled()

    def _line(self, prompt: str) -> str | None:
        """A typed answer read key by key, so Esc goes back at once (None) without Enter.

        Backspace deletes, Ctrl+U clears the line, Ctrl+D quits. A long answer scrolls
        within the line instead of wrapping.
        """
        width = shutil.get_terminal_size((100, 30)).columns
        if text_width(prompt) > width - 20:
            self.say(prompt.rstrip())  # the question on its own line, the answer below it
            prompt = "> "
        text = ""
        with raw_keys():
            while True:
                self.out.write("\r\x1b[K" + prompt + _tail(text, width - 1 - text_width(prompt)))
                self.out.flush()
                key = read_key()
                if key in ("enter", "esc", "eof"):
                    self.out.write("\n")
                    if key == "eof":
                        raise Cancelled()
                    return text.strip() if key == "enter" else None
                if key == "backspace":
                    text = text[:-1]
                elif key == "\x15":
                    text = ""
                elif len(key) == 1 and key.isprintable():
                    text += key

    def _clear_for_quit(self) -> None:
        # The visible screen only; the scrollback keeps what setup printed.
        self.out.write(CLEAR)
        self.out.flush()

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
        if self.assume_yes:
            return super().choose(
                title, headers, choices, default=default, keys=keys, notice=notice, **nav
            )
        keys = with_nav_keys(keys or {}, **nav)
        if not choices:
            # Only actions: printed where the screen is, so what came before stays in view,
            # and each takes one key press.
            self._print_menu(title, headers, choices, keys, notice)
            return self._menu_key(keys)
        selected = default
        top = 0
        refused = ""  # said once after Enter on a greyed-out row, so it never looks stuck
        arrows = arrows_for(self.out)
        click = _shown_hint(choices, click_hint())
        pages = alternates(previous_page, next_page, arrows)
        self.out.write(ALT_SCREEN_ON)
        try:
            with raw_keys():
                while True:
                    size = shutil.get_terminal_size((100, 30))
                    shown = "\n".join(text for text in (notice, refused) if text)
                    notes = len(notice_lines(shown, size.columns))
                    tallest = detail_height(choices, size.columns)
                    body = body_height(
                        size.lines, keys, click, pages, notes, tallest, _gaps(choices)
                    )
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
                        shown,
                    )
                    self.out.write(CLEAR + "\r\n".join(lines))
                    self.out.flush()
                    key = _next_key(size)
                    if not key:
                        continue  # the window was resized: draw it again at the new size
                    refused = ""
                    if key == "enter" and not choices[selected].enabled:
                        refused = "That option is not available here; its note says why."
                    elif key in ("up", "k"):
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
                    elif key in keys:  # Esc is Back wherever a menu offers it
                        return key
                    elif key in ("q", "esc", "eof"):
                        raise Cancelled()
        finally:
            self.out.write(ALT_SCREEN_OFF)
            self.out.flush()
