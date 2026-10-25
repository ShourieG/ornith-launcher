"""Generate ``docs/BENCHMARKS.md`` from ``data/tested.json``; numbers are never edited by hand.

python3 -m ornith_launcher.bench.report docs/BENCHMARKS.md
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ornith_launcher.planner.rank import TESTED_FILE
from ornith_launcher.resources import load_json

HEADER = """# Benchmarks

Generated from `src/ornith_launcher/data/tested.json` by `python3 -m ornith_launcher.bench.report`;
do not edit by hand.

Every number is labelled: TESTED (measured), INFERRED (derived from measured runs of the
same architecture; the method is stated) or ESTIMATED (calculated only, never run). Results come
from one machine and are no guarantee for others.
"""
SECTIONS = (
    ("perplexity", "Short-context quality: perplexity"),
    ("needle", "Long-context retrieval: multi-needle"),
    ("prefill", "Prefill speed"),
    ("memory", "Memory"),
)


def _table(section: Mapping[str, Any]) -> list[str]:
    columns = list(section["columns"])
    lines = ["| Run | " + " | ".join(columns) + " | Level |"]
    lines.append("|" + "---|" * (len(columns) + 2))
    for row in section["rows"]:
        levels = {level for level in row["levels"] if level}
        uniform = len(levels) == 1
        cells = []
        for value, level in zip(row["values"], row["levels"]):
            if value is None:
                cells.append("n/a")
            else:
                cells.append(str(value) if uniform else f"{value} ({level})")
        level_text = next(iter(levels)) if uniform else "mixed"
        lines.append(f"| {row['model']} | " + " | ".join(cells) + f" | {level_text} |")
    return lines


def render(data: Mapping[str, Any]) -> str:
    bench = data["benchmarks"]
    lines = [HEADER.rstrip(), "", "## Setup", "", bench["setup"], ""]
    for name, model in bench["models"].items():
        lines.append(f"- {name}: `{model}`")
    lines += ["", bench["scope"], ""]
    for key, title in SECTIONS:
        section = bench[key]
        lines += [f"## {title}", "", section["method"], ""]
        lines += _table(section)
        if section.get("note"):
            lines += ["", section["note"]]
        lines.append("")
    lines += ["## Thermals", "", bench["thermals"], "", "## Not measured yet", ""]
    lines += [f"- {item}" for item in bench["backlog"]]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    text = render(load_json(TESTED_FILE))
    if argv:
        Path(argv[0]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
