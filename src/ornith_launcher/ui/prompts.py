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

from ornith_launcher.ui.plain import table

# After a yes/no question, naming the answer Enter gives.
YES_HINT = "[Y/n] (default: yes)"
NO_HINT = "[y/N] (default: no)"

# Navigation keys a menu can offer; choose() returns the key when one is picked.
NEXT_PAGE = "n"
PREVIOUS_PAGE = "p"
BACK = "b"
MAIN_MENU = "m"


class Cancelled(Exception):
    """The user quit (q, Ctrl+D) or interrupted (Ctrl+C) at a prompt."""


@dataclass(frozen=True)
class Choice:
    """A selectable row; ``cells`` line up under the list's headers."""

    cells: tuple[str, ...]
    detail: str = ""  # one more line shown under the list (plain) or for the highlighted row
    enabled: bool = True
    link: str = ""  # web page the first cell opens when clicked (full screen only)


@dataclass
class PlainPrompter:
    read_line: Callable[[str], str] = input
    out: TextIO = field(default_factory=lambda: sys.stdout)
    assume_yes: bool = False  # --yes: take every default without asking

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def _ask(self, prompt: str) -> str:
        try:
            return self.read_line(prompt).strip()
        except EOFError:
            raise Cancelled() from None

    # Layout: a screen is a bold title, then groups of lines with a blank line between
    # them. Every question gets a blank line before it, so callers never print one.

    def confirm(self, question: str, default: bool = True) -> bool:
        if self.assume_yes:
            return default
        self.say()
        hint = YES_HINT if default else NO_HINT
        while True:
            answer = self._ask(f"{question} {hint} ").lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.say("Please answer y or n.")

    def text(self, question: str, default: str = "") -> str:
        if self.assume_yes:
            return default
        self.say()
        suffix = f" [{default}]" if default else ""
        answer = self._ask(f"{question}{suffix}: ")
        return answer or default

    def bold(self, text: str) -> str:
        """``text`` in bold where the terminal shows it (the full-screen prompter)."""
        return text

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
        if getattr(self.out, "isatty", lambda: False)():
            width = min(shutil.get_terminal_size((100, 30)).columns, 100) - 2
            text = textwrap.fill(
                text, max(40, width), initial_indent=indent, subsequent_indent=indent
            )
        else:
            text = indent + text
        self.say(text)

    def typed_confirmation(self, warning: str, phrase: str) -> bool:
        """Ask the user to type ``phrase``, or BACK to decline. ``--yes`` never answers this."""
        if self.assume_yes:
            return False
        self.paragraph(warning)
        self.say()
        while True:
            answer = self._ask(
                f'Type "{phrase}" and press Enter to continue, or {BACK} to go back: '
            )
            if answer.lower() == BACK:
                return False
            if same_words(answer, phrase):
                return True
            self.say(f"Type {phrase} (quotes are optional) to continue, or {BACK} to go back.")

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
        self.say()
        self.say(self.bold(title))
        if notice:
            self.paragraph(notice)
        rows = [(str(i + 1), *c.cells) for i, c in enumerate(choices)]
        if rows:
            self.say()
            self.say(table(["#", *headers], rows))
        for index, choice in enumerate(choices, 1):
            if choice.detail:
                self.say(f"  {index}. {choice.detail}")
        self.say()
        self.say("  Actions")
        self.say()
        for line in key_help([*((label, key) for key, label in keys.items()), ("quit", "q")]):
            self.say("  " + line)
        default_text = str(default + 1) if choices else ""
        while True:
            answer = self._ask(f"Choose{f' [{default_text}]' if default_text else ''}: ").lower()
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


def key_help(entries: Sequence[tuple[str, str]]) -> list[str]:
    """One ``(action, key)`` per line, action first, with the keys lined up in a column."""
    width = max(len(action) for action, _ in entries)
    return [f"{(action[:1].upper() + action[1:]).ljust(width)}  {key}" for action, key in entries]


def invalid_choice_hint(count: int, letters: Sequence[str]) -> str:
    """What to type instead; a menu of letters only must not ask for a number from 1 to 0."""
    if count == 0:
        return f"Please enter {', '.join(letters[:-1])} or {letters[-1]}."
    if count == 1:
        return "Please enter 1, or a letter."
    return f"Please enter a number from 1 to {count}, or a letter."
