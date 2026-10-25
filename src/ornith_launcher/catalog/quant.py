"""Quantization labels for GGUF files."""

from __future__ import annotations

import re
from collections.abc import Iterable

# llama.cpp's llama_ftype values (include/llama.h at build b11552), stored as general.file_type.
FILE_TYPE_NAMES = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    7: "Q8_0",
    8: "Q5_0",
    9: "Q5_1",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS",
    20: "IQ2_XS",
    21: "Q2_K_S",
    22: "IQ3_XS",
    23: "IQ3_XXS",
    24: "IQ1_S",
    25: "IQ4_NL",
    26: "IQ3_S",
    27: "IQ3_M",
    28: "IQ2_S",
    29: "IQ2_M",
    30: "IQ4_XS",
    31: "IQ1_M",
    32: "BF16",
    36: "TQ1_0",
    37: "TQ2_0",
    38: "MXFP4_MOE",
    39: "NVFP4",
    40: "Q1_0",
    41: "Q2_0",
}

# A quant token bounded by non-alphanumerics, e.g. "-Q4_K_M." or "/UD-IQ1_S-".
# Unsloth's "UD-" (dynamic) quants differ from the plain ones, so the prefix is kept.
_QUANT_IN_NAME = re.compile(
    r"(?<![A-Za-z0-9])"
    r"((?:UD-)?(?:IQ\d_[A-Z]+|Q\d_K(?:_[A-Z]+)?|Q\d_\d|TQ\d_\d|BF16|F16|F32|MXFP4(?:_MOE)?|NVFP4))"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def quant_from_name(name: str) -> str | None:
    """Return the quant label in a file name or path, upper-cased, or None.

    The last match wins, because names such as ``Ornith-1.5-35B-A3B-Q8_0.gguf``
    put the quant at the end.
    """
    matches = _QUANT_IN_NAME.findall(name)
    return matches[-1].upper() if matches else None


def quant_from_file_type(file_type: object) -> str | None:
    """Return the quant label for a ``general.file_type`` value, or None if unknown."""
    if isinstance(file_type, int) and not isinstance(file_type, bool):
        return FILE_TYPE_NAMES.get(file_type)
    return None


def is_low_bit(quant: str | None, low_bit_prefixes: Iterable[str]) -> bool:
    """True for quants below roughly 3 bits per weight (IQ1_*, IQ2_*, Q2_K, ...)."""
    if quant is None:
        return False
    base = quant.upper()
    if base.startswith("UD-"):
        base = base[len("UD-") :]
    return base.startswith(tuple(prefix.upper() for prefix in low_bit_prefixes))
