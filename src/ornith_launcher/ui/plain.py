"""Plain-text rendering: tables and summaries that work in any terminal, pipe or log."""

from __future__ import annotations

from collections.abc import Sequence

GIB = 1024**3


def gib(value: int | float) -> str:
    return f"{value / GIB:.1f} GiB"


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
_CONTEXT_LABELS = {65536: "64k", 131072: "128k", 262144: "262k", 524288: "524k", 1048576: "1M"}


def context_label(tokens: int) -> str:
    """262144 -> "262k", 1048576 -> "1M"."""
    return _CONTEXT_LABELS.get(tokens, f"{tokens // 1000}k")


def table(headers: Sequence[str], rows: Sequence[Sequence[str]], *, max_width: int = 0) -> str:
    """Left-aligned columns separated by two spaces; the last column is cut to ``max_width``."""
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = []
    for row in [list(headers), *map(list, rows)]:
        cells = [cell.ljust(widths[i]) for i, cell in enumerate(row)]
        line = "  ".join(cells).rstrip()
        if max_width and len(line) > max_width:
            line = line[: max_width - 1] + "…"
        lines.append(line)
    return "\n".join(lines)
