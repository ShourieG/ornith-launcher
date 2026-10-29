"""Plain-text rendering: tables and summaries that work in any terminal, pipe or log."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

GIB = 1024**3
_STYLE = re.compile(r"\x1b\[[0-9;]*m")  # bold, reverse and colour codes take no columns


def gib(value: int | float) -> str:
    return f"{value / GIB:.1f} GiB"


def clean_cell(text: str) -> str:
    """``text`` on one line: tabs, newlines and other control characters become spaces."""
    return "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in text)


def _char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(text: str) -> int:
    """Terminal columns ``text`` takes: wide (CJK) characters take two, combining ones and
    colour or bold codes none."""
    return sum(_char_width(ch) for ch in _STYLE.sub("", text))


def wrap_styled(text: str, width: int, indent: str = "") -> list[str]:
    """Greedy word wrap that counts columns as ``text_width`` does, so bold or colour codes
    take no room (``textwrap`` counts them as characters and wraps too early)."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}" if line else f"{indent}{word}"
        if line and text_width(candidate) > width:
            lines.append(line)
            candidate = f"{indent}{word}"
        line = candidate
    return [*lines, line] if line else lines


def pad(text: str, width: int) -> str:
    """``text`` padded with spaces to ``width`` columns."""
    return text + " " * max(0, width - text_width(text))


def clip(text: str, width: int, marker: str = "") -> str:
    """``text`` cut to at most ``width`` columns, ending in ``marker`` when it was cut."""
    if text_width(text) <= width:
        return text
    room = width - text_width(marker)
    kept, used = [], 0
    for ch in text:
        if used + _char_width(ch) > room:
            break
        kept.append(ch)
        used += _char_width(ch)
    return "".join(kept) + marker


def column_widths(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[int]:
    """The width of each column: its widest cell or header, in terminal columns."""
    widths = [text_width(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], text_width(cell))
    return widths


def table_line(cells: Sequence[str], widths: Sequence[int]) -> str:
    """One row: each cell padded to its column, two spaces between columns."""
    return "  ".join(pad(cell, widths[i]) for i, cell in enumerate(cells)).rstrip()


def key_values(rows: Sequence[tuple[str, str]], indent: str = "  ") -> list[str]:
    """Aligned "Label:  value" lines, e.g. a summary."""
    width = max(len(label) for label, _ in rows) + 1
    return [f"{indent}{(label + ':').ljust(width)}  {value}" for label, value in rows]


def compact_count(value: int) -> str:
    """554 -> "554", 12345 -> "12.3k", 3860800 -> "3.9M"."""
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        # 999,960 rounds up to "1M", not "1000k".
        if value >= limit or (value >= 1000 and round(value / limit, 1) >= 1):
            text = f"{value / limit:.1f}".removesuffix(".0")
            return f"{text}{suffix}"
    return str(value)


# How model cards and users name the context sizes on offer.
_CONTEXT_LABELS = {
    32768: "32k",
    65536: "64k",
    131072: "128k",
    262144: "262k",
    524288: "524k",
    1048576: "1M",
}


def context_label(tokens: int) -> str:
    """262144 -> "262k", 1048576 -> "1M"."""
    return _CONTEXT_LABELS.get(tokens, f"{tokens // 1000}k")


def table(headers: Sequence[str], rows: Sequence[Sequence[str]], *, max_width: int = 0) -> str:
    """Left-aligned columns separated by two spaces; each line is cut to ``max_width``.

    Widths count terminal columns, so wide characters cannot push a column out of line.
    """
    headers = [clean_cell(h) for h in headers]
    rows = [[clean_cell(cell) for cell in row] for row in rows]
    widths = column_widths(headers, rows)
    lines = []
    for row in [headers, *rows]:
        line = table_line(row, widths)
        if max_width:
            line = clip(line, max_width, "…")
        lines.append(line)
    return "\n".join(lines)
