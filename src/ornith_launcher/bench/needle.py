"""Multi-needle long-context retrieval against a running server.

Six "vault access code" sentences sit at fixed fractions of a long document.
Each test sends a prefix of increasing length plus a question asking for every
code, and scores how many codes inside the prefix come back exactly. Lengths
run in increasing order on one slot, so the prompt cache only processes the
new part each time. It is an easy test (exact string retrieval); it does not
show reasoning quality over long context.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

MAX_DOCUMENT_TOKENS = 950_000
FRACTIONS = (0.03, 0.20, 0.40, 0.60, 0.80, 0.95)
QUESTION = (
    "\n\n---\nThe text above is a long collection of software documentation. Hidden in it are "
    "a few notes that give access codes for numbered vaults. List every vault and its access "
    "code that appears in the text, one per line, exactly in the form `vault N: CODE`. Only "
    "list codes that actually appear above; do not guess."
)
_ANSWER = re.compile(r"vault\s*(\d+)\s*[:=-]\s*`?([A-Za-z]+-\d{4}-[A-Za-z])", re.IGNORECASE)


@dataclass(frozen=True)
class Needle:
    vault: int
    code: str
    fraction: float

    @property
    def text(self) -> str:
        return f"Archive note: the access code for vault {self.vault} is {self.code}."


@dataclass
class Document:
    text: str
    needles: list[Needle]
    positions: dict[int, int] = field(default_factory=dict)  # vault -> character offset
    chars_per_token: float = 4.0

    def prefix(self, tokens: int) -> tuple[str, list[Needle]]:
        """The document cut at a paragraph border near ``tokens``, and the needles inside it."""
        cut = self.text.rfind("\n\n", 0, int(tokens * self.chars_per_token))
        cut = cut if cut > 0 else min(len(self.text), int(tokens * self.chars_per_token))
        inside = [n for n in self.needles if self.positions.get(n.vault, len(self.text)) < cut]
        return self.text[:cut], inside


def make_needles(seed: int = 11) -> list[Needle]:
    rng = random.Random(seed)
    colours = ["AMBER", "COBALT", "LILAC", "OCHRE", "SLATE", "TEAL"]
    return [
        Needle(i, f"{rng.choice(colours)}-{rng.randint(1000, 9999)}-{rng.choice('KMRTXZ')}", f)
        for i, f in enumerate(FRACTIONS, 1)
    ]


def make_document(
    paragraphs: Sequence[str],
    chars_per_token: float,
    max_tokens: int = MAX_DOCUMENT_TOKENS,
    seed: int = 7,
) -> Document:
    """The same document for every run: shuffled paragraphs with needles at fixed fractions."""
    order = list(paragraphs)
    random.Random(seed).shuffle(order)
    needles = make_needles()
    parts: list[str] = []
    positions: dict[int, int] = {}
    position = 0
    pending = list(needles)
    limit = max_tokens * chars_per_token
    for paragraph in order:
        while pending and position >= pending[0].fraction * limit:
            needle = pending.pop(0)
            positions[needle.vault] = position
            parts.append(needle.text)
            position += len(needle.text) + 2
        parts.append(paragraph)
        position += len(paragraph) + 2
        if position > limit * 1.02 and not pending:
            break
    return Document("\n\n".join(parts), needles, positions, chars_per_token)


def score(answer: str, inside: Sequence[Needle]) -> dict[str, Any]:
    found = {int(m.group(1)): m.group(2).upper() for m in _ANSWER.finditer(answer)}
    correct = [n.vault for n in inside if found.get(n.vault) == n.code]
    wrong = {vault: code for vault, code in found.items() if vault not in correct}
    return {
        "needles_inside": [n.vault for n in inside],
        "correct": correct,
        "wrong_or_invented": wrong,
        "score": f"{len(correct)}/{len(inside)}",
    }


def request_body(model: str, prompt: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt + QUESTION}],
        "temperature": 0,
        "max_tokens": 400,
        "cache_prompt": True,
        "id_slot": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def run_length(
    post: Callable[[str, dict], dict], model: str, document: Document, tokens: int
) -> dict[str, Any]:
    """One test at one length; ``post(path, body)`` talks to the server."""
    prefix, inside = document.prefix(tokens)
    try:
        reply = post("/v1/chat/completions", request_body(model, prefix))
        error = None
    except Exception as exc:  # the result records the failure; the series goes on
        reply, error = {}, f"{type(exc).__name__}: {exc}"
    choices = reply.get("choices") or [{}]
    answer = (choices[0].get("message") or {}).get("content") or ""
    timings = reply.get("timings") or {}
    return {
        "target_tokens": tokens,
        "prompt_tokens": (reply.get("usage") or {}).get("prompt_tokens"),
        **score(answer, inside),
        "new_prompt_tokens": timings.get("prompt_n"),
        "prompt_s": round(timings.get("prompt_ms", 0) / 1000, 1),
        "prefill_tok_s": round(timings.get("prompt_per_second", 0), 1),
        "error": error,
        "answer": answer.strip()[:600],
    }
