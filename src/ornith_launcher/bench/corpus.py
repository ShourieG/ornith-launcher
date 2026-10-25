"""Test text for the benchmarks, built on the user's machine from its own documentation.

The text comes from ``/usr/share/doc`` (README, NEWS and similar files). It has
mixed licences, so it is built locally and never shipped. Results therefore
compare across runs on one machine, not across machines.
"""

from __future__ import annotations

import gzip
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

MIN_PARAGRAPH = 200
PROSE_PREFIXES = ("readme", "news", "faq", "authors")
PERPLEXITY_BYTES = 640_000  # about 160k tokens; the size used on the DGX Spark


@dataclass(frozen=True)
class Corpus:
    haystack: Path  # all paragraphs, for the needle test
    prose: Path  # English prose, for perplexity
    perplexity: Path  # the first PERPLEXITY_BYTES of the prose


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > MIN_PARAGRAPH]


def is_prose(paragraph: str) -> bool:
    letters = sum(c.isalpha() or c.isspace() for c in paragraph)
    return letters / len(paragraph) > 0.9


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in items if not (x in seen or seen.add(x))]


def _read(path: Path) -> str:
    try:
        raw = gzip.open(path).read() if path.suffix == ".gz" else path.read_bytes()
    except (OSError, EOFError):
        return ""
    return raw.decode("utf-8", "ignore")


def _files(root: Path) -> Iterator[Path]:
    for folder, _, names in sorted(os.walk(root)):
        for name in sorted(names):
            yield Path(folder) / name


def build_corpus(out: Path, source: Path = Path("/usr/share/doc")) -> Corpus:
    """Write the corpus files into ``out`` (skipped if they already exist)."""
    corpus = Corpus(out / "haystack.txt", out / "prose.txt", out / "ppl.txt")
    if all(p.exists() for p in (corpus.haystack, corpus.prose, corpus.perplexity)):
        return corpus
    haystack: list[str] = []
    prose: list[str] = []
    for path in _files(source):
        text = _read(path)
        # The needles are the only vault notes in the document.
        if not text or "vault" in text.lower():
            continue
        found = paragraphs(text)
        if path.name.lower().startswith(PROSE_PREFIXES):
            prose += [p for p in found if is_prose(p)]
        haystack += found
    out.mkdir(parents=True, exist_ok=True)
    corpus.haystack.write_text("\n\n".join(unique(haystack)), encoding="utf-8")
    prose_text = "\n\n".join(unique(prose))
    corpus.prose.write_text(prose_text, encoding="utf-8")
    corpus.perplexity.write_text(prose_text[:PERPLEXITY_BYTES], encoding="utf-8")
    return corpus
