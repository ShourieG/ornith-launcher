"""Access to the data files shipped inside the package (``ornith_launcher/data``)."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from ornith_launcher.errors import DataFileError

_REINSTALL_HINT = "Re-download ornith-launcher; the package files look incomplete."


def load_text(name: str) -> str:
    """Return the contents of ``data/<name>``."""
    path = resources.files("ornith_launcher") / "data" / name
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DataFileError(f"data file {name} is missing", hint=_REINSTALL_HINT) from exc


def load_json(name: str) -> Any:
    """Return the parsed contents of ``data/<name>``."""
    path = resources.files("ornith_launcher") / "data" / name
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DataFileError(f"data file {name} is missing", hint=_REINSTALL_HINT) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataFileError(f"data file {name} is not valid JSON: {exc}") from exc
