"""Asking the user: numbered menus and yes/no questions that work in any terminal.

Every screen of the setup wizard goes through a Prompter. The plain one reads
lines from stdin, so it also works over SSH, in pipes and in tests; the
full-screen one (``ui.tui``) only replaces how lists are picked from.
"""

from __future__ import annotations

import shutil
import sys
import textwrap
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TextIO

from ornith_launcher.ui.plain import (
    clean_cell,
    clip,
    column_widths,
    table_line,
    text_width,
    wrap_styled,
)

# After a yes/no question, naming the answer Enter gives.
YES_HINT = "[Y/n] (default: yes)"
NO_HINT = "[y/N] (default: no)"

# Navigation keys a menu can offer; choose() returns the key when one is picked.
NEXT_PAGE = "n"
PREVIOUS_PAGE = "p"
BACK = "esc"  # the Esc key; in plain menus, press Esc then Enter, or type esc
MAIN_MENU = "m"


class Cancelled(Exception):
    """The user quit (q, Ctrl+D) or interrupted (Ctrl+C) at a prompt."""


@dataclass(frozen=True)
class Choice:
    """A selectable row; ``cells`` line up under the list's headers.

    ``detail`` is a note on the row; ``info`` gives it as labelled lines instead, each
    label in bold. Both are shown under the list (plain) or for the highlighted row.
    """

    cells: tuple[str, ...]
    detail: str = ""
    enabled: bool = True
    link: str = ""  # web page the first cell opens when clicked, shown as "URL - <link>"
    info: tuple[tuple[str, str], ...] = ()  # (label, text) lines
    # An action such as Continue rather than a row of data: shown in bold after a blank
    # line, its first cell only, and left out when the columns are sized.
    action: bool = False


def list_widths(headers: Sequence[str], choices: Sequence[Choice]) -> list[int]:
    """Column widths from the headers and the data rows; action rows do not count."""
    data = [[clean_cell(cell) for cell in c.cells] for c in choices if not c.action]
    return column_widths([clean_cell(h) for h in headers], data)


def is_back(raw: str) -> bool:
    """True for Esc typed into a line (it arrives as an escape character) or the word esc."""
    return "\x1b" in raw or raw.strip().lower() == BACK


def detail_lines(choice: Choice, width: int, bold: Callable[[str], str]) -> list[str]:
    """The lines under a list for ``choice``: its notes wrapped to ``width``, then its URL.

    Labels line up in a column and wrapped text stays under its own column. The URL is
    never cut, so terminals can still find it, and a blank line comes before it.
    """
    width = max(20, width)
    lines: list[str] = []
    if choice.info:
        label_width = max(text_width(label) for label, _ in choice.info)
        hang = label_width + 2
        for label, text in choice.info:
            rows = textwrap.wrap(clean_cell(text), max(10, width - hang)) or [""]
            name = bold(label) + " " * (hang - text_width(label))
            lines.append(name + rows[0])
            lines.extend(" " * hang + row for row in rows[1:])
    elif choice.detail:
        lines.extend(textwrap.wrap(clean_cell(choice.detail), width))
    if choice.link:
        if lines:
            lines.append("")
        lines.append(f"{bold('URL')} - {choice.link}")
    return lines


@dataclass
class PlainPrompter:
    read_line: Callable[[str], str] = input
    out: TextIO = field(default_factory=lambda: sys.stdout)
    assume_yes: bool = False  # --yes: take every default without asking

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def _read(self, prompt: str) -> str:
        try:
            return self.read_line(prompt)
        except EOFError:
            raise Cancelled() from None

    def _line(self, prompt: str) -> str | None:
        """A typed answer, stripped; None when it is Esc (here, Esc then Enter, or "esc")."""
        raw = self._read(prompt)
        return None if is_back(raw) else raw.strip()

    # Layout: a screen is a bold title, then groups of lines with a blank line between
    # them. Every question gets a blank line before it, so callers never print one.

    def confirm(self, question: str, default: bool = True, back: bool = False) -> bool | None:
        """Yes or no, the question in bold. Esc answers no, or with ``back`` returns None."""
        if self.assume_yes:
            return default
        self.say()
        hint = YES_HINT if default else NO_HINT
        if back:
            hint = f"{hint[:-1]}; {shown_key(BACK)} to go back)"
        while True:
            answer = self._line(f"{self.bold(question)} {hint} ")
            if answer is None:
                return None if back else False
            answer = answer.lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.say("Please answer y or n.")

    def pause(self, message: str) -> None:
        """Wait for Enter (or Esc) so the lines above can be read before the screen changes."""
        if self.assume_yes:
            return
        self.say()
        self._line(message)

    def text(self, question: str, default: str = "", back: bool = False) -> str:
        """A typed answer, or ``default`` for Enter alone.

        Esc keeps ``default``, or with ``back`` returns BACK so the caller can go back.
        """
        if self.assume_yes:
            return default
        self.say()
        suffix = f" [{default}]" if default else ""
        answer = self._line(f"{question}{suffix}: ")
        if answer is None:
            return BACK if back else default
        return answer or default

    def bold(self, text: str) -> str:
        """``text`` in bold where the terminal shows it (the full-screen prompter)."""
        return text

    def quit_screen(self, lines: Sequence[str]) -> None:
        """How every quit ends: a clear screen where there is one, then ``lines``.

        The plain prompter (pipes, SSH, --plain) clears nothing; a blank line sets the
        message apart from the last question instead.
        """
        self._clear_for_quit()
        for line in lines:
            self.say(line)

    def _clear_for_quit(self) -> None:
        self.say()

    def new_screen(self, title: str) -> None:
        """Start a setup step under ``title``; the full-screen prompter clears the screen first."""
        self.say()
        self.say(self.bold(title))
        self.say()

    def section(self, label: str) -> None:
        """A group of lines inside a screen: a blank line, then the bold label."""
        self.say()
        self.say(self.bold(label))

    def paragraph(self, text: str, indent: str = "") -> None:
        """A block of text, wrapped to the terminal when there is one (logs keep one line)."""
        if self._is_tty():
            width = min(shutil.get_terminal_size((100, 30)).columns, 100) - 2
            width = max(40, width)
            text = "\n".join(
                "\n".join(wrap_styled(line, width, indent))
                if text_width(line) != len(line)  # style codes: textwrap would count them
                else textwrap.fill(line, width, initial_indent=indent, subsequent_indent=indent)
                for line in text.split("\n")
            )
        else:
            text = "\n".join(indent + line for line in text.split("\n"))
        self.say(text)

    def _is_tty(self) -> bool:
        return getattr(self.out, "isatty", lambda: False)()

    def typed_confirmation(self, warning: str, phrase: str) -> bool:
        """Ask the user to type ``phrase``, or BACK to decline. ``--yes`` never answers this."""
        if self.assume_yes:
            return False
        self.paragraph(warning)
        self.say()
        while True:
            back = shown_key(BACK)
            raw = self._line(f'Type "{phrase}" and press Enter to continue, or {back} to go back: ')
            if raw is None:
                return False
            if same_words(raw, phrase):
                return True
            self.say(f"Type {phrase} (quotes are optional) to continue, or {back} to go back.")

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
        """Pick a row (returns its index) or one of ``keys`` (returns the key).

        ``keys`` maps single letters to actions, e.g. {"a": "show all models"}.
        ``notice`` is shown under the title. Each navigation flag adds its key
        (PREVIOUS_PAGE, NEXT_PAGE, BACK, MAIN_MENU), which is then returned like any
        other key. Enter picks ``default``; q quits.
        """
        keys = with_nav_keys(keys or {}, previous_page, next_page, back, main_menu)
        if self.assume_yes and choices and choices[default].enabled:
            return default
        self._print_menu(title, headers, choices, keys, notice)
        default_text = str(default + 1) if choices else ""
        while True:
            raw = self._read(f"Choose{f' [{default_text}]' if default_text else ''}: ")
            if is_back(raw) and BACK in keys:
                return BACK
            answer = raw.strip().lower()
            if not answer and choices:
                answer = default_text
            if answer == "q":
                raise Cancelled()
            if answer in keys:
                return answer
            if answer.isdigit() and 1 <= int(answer) <= len(choices):
                if choices[int(answer) - 1].enabled:
                    return int(answer) - 1
                self.say("That option is not available here.")
                continue
            self.say(invalid_choice_hint(len(choices), [*keys, "q"]))

    def _print_menu(
        self,
        title: str,
        headers: Sequence[str],
        choices: Sequence[Choice],
        keys: dict[str, str],
        notice: str,
    ) -> None:
        """A menu as plain lines: title, notice, numbered rows, their notes and the actions."""
        self.say()
        self.say(self.bold(title))
        if notice:
            self.paragraph(notice)
        if choices:
            self.say()
            # Cut to the terminal so a long row never wraps and pulls the columns apart.
            width = shutil.get_terminal_size((0, 0)).columns if self._is_tty() else 0
            numbered = [
                Choice((str(i + 1), *c.cells), action=c.action) for i, c in enumerate(choices)
            ]
            widths = list_widths(["#", *headers], numbered)

            def cut(line: str) -> str:
                return clip(line, width, "…") if width else line

            self.say(cut(table_line([clean_cell(h) for h in ["#", *headers]], widths)))
            for row in numbered:
                cells = [clean_cell(cell) for cell in row.cells]
                if row.action:
                    self.say()  # an action stands apart from the rows of data
                    self.say(self.bold(cut(f"{cells[0].ljust(widths[0])}  {cells[1]}")))
                else:
                    self.say(cut(table_line(cells, widths)))
        width = min(shutil.get_terminal_size((100, 30)).columns, 100) if self._is_tty() else 100
        for index, choice in enumerate(choices, 1):
            lines = detail_lines(choice, width - 6, self.bold)
            if not lines:
                continue
            self.say()  # each row's notes stand apart from the table and from each other
            number = f"{index}.".ljust(4)
            self.say(f"  {number}{lines[0]}")
            for line in lines[1:]:
                self.say(f"      {line}" if line else "")
        self.say()
        self.say("  Actions")
        self.say()
        entries = [(label, shown_key(key)) for key, label in keys.items()]
        for line in key_help([*entries, ("quit", "q")]):
            self.say("  " + line)


def same_words(answer: str, phrase: str) -> bool:
    """True when ``answer`` is ``phrase``, ignoring quotes, capitals and extra spaces."""

    def words(text: str) -> list[str]:
        return text.strip().strip("\"'\u201c\u201d\u2018\u2019").casefold().split()

    return words(answer) == words(phrase)


def with_nav_keys(
    keys: dict[str, str],
    previous_page: bool = False,
    next_page: bool = False,
    back: bool = False,
    main_menu: bool = False,
) -> dict[str, str]:
    """``keys`` plus the navigation keys the menu offers, after its own actions."""
    keys = dict(keys)
    for wanted, key, label in (
        (next_page, NEXT_PAGE, "next page"),
        (previous_page, PREVIOUS_PAGE, "previous page"),
        (back, BACK, "back"),
        (main_menu, MAIN_MENU, "main menu"),
    ):
        if wanted:
            if key in keys:
                raise ValueError(f"{key!r} is a navigation key; give {keys[key]!r} another letter")
            keys[key] = label
    return keys


def shown_key(key: str) -> str:
    """How the actions list names a key: "Esc" for BACK, the letter itself otherwise."""
    return "Esc" if key == BACK else key


def key_help(entries: Sequence[tuple[str, str]]) -> list[str]:
    """One ``(action, key)`` per line, action first, with the keys lined up in a column."""
    width = max(len(action) for action, _ in entries)
    return [f"{(action[:1].upper() + action[1:]).ljust(width)}  {key}" for action, key in entries]


def invalid_choice_hint(count: int, letters: Sequence[str]) -> str:
    """What to type instead; a menu of letters only must not ask for a number from 1 to 0."""
    letters = [shown_key(letter) for letter in letters]
    if count == 0:
        return f"Please enter {', '.join(letters[:-1])} or {letters[-1]}."
    if count == 1:
        return "Please enter 1, or a letter."
    return f"Please enter a number from 1 to {count}, or a letter."
