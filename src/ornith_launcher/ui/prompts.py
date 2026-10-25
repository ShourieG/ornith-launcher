"""Asking the user: numbered menus and yes/no questions that work in any terminal.

Every screen of the setup wizard goes through a Prompter. The plain one reads
lines from stdin, so it also works over SSH, in pipes and in tests; the
full-screen one (``ui.tui``) only replaces how lists are picked from.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TextIO

from ornith_launcher.ui.plain import table


class Cancelled(Exception):
    """The user quit (q, Ctrl+D) or interrupted (Ctrl+C) at a prompt."""


@dataclass(frozen=True)
class Choice:
    """A selectable row; ``cells`` line up under the list's headers."""

    cells: tuple[str, ...]
    detail: str = ""  # one more line shown under the list (plain) or for the highlighted row
    enabled: bool = True


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

    def confirm(self, question: str, default: bool = True) -> bool:
        if self.assume_yes:
            return default
        hint = "[Y/n]" if default else "[y/N]"
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
        suffix = f" [{default}]" if default else ""
        answer = self._ask(f"{question}{suffix}: ")
        return answer or default

    def typed_confirmation(self, warning: str, phrase: str) -> bool:
        """Ask the user to type ``phrase`` exactly; ``--yes`` never answers this."""
        self.say(warning)
        return self._ask(f'Type "{phrase}" to continue, or press Enter to go back: ') == phrase

    def choose(
        self,
        title: str,
        headers: Sequence[str],
        choices: Sequence[Choice],
        *,
        default: int = 0,
        keys: dict[str, str] | None = None,
    ) -> int | str:
        """Pick a row (returns its index) or one of ``keys`` (returns the key).

        ``keys`` maps single letters to actions, e.g. {"a": "show all models"}.
        Enter picks ``default``; q quits.
        """
        keys = keys or {}
        if self.assume_yes and choices and choices[default].enabled:
            return default
        self.say()
        self.say(title)
        rows = [(str(i + 1), *c.cells) for i, c in enumerate(choices)]
        if rows:
            self.say(table(["#", *headers], rows))
        for index, choice in enumerate(choices, 1):
            if choice.detail:
                self.say(f"  {index}. {choice.detail}")
        options = [f"{key} = {label}" for key, label in keys.items()] + ["q = quit"]
        self.say("  " + ", ".join(options))
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
            self.say(
                f"Please enter a number from 1 to {len(choices)}"
                + (", or a letter." if keys else ".")
            )
