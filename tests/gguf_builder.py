"""Build GGUF metadata in memory for tests.

Real headers are third-party files and contain chat-template text, so they are not
committed; these builders reproduce the architecture keys of the official Ornith
headers (read 2026-10-18) instead.
"""

from __future__ import annotations

import struct
from typing import Any

from ornith_launcher.catalog.gguf import ValueType

# Written out independently of the parser's table, so a mistake there is caught.
_FORMATS = {
    ValueType.UINT8: "B",
    ValueType.INT8: "b",
    ValueType.UINT16: "H",
    ValueType.INT16: "h",
    ValueType.UINT32: "I",
    ValueType.INT32: "i",
    ValueType.FLOAT32: "f",
    ValueType.BOOL: "?",
    ValueType.UINT64: "Q",
    ValueType.INT64: "q",
    ValueType.FLOAT64: "d",
}


def _string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _value(value_type: ValueType, value: Any) -> bytes:
    if value_type is ValueType.STRING:
        return _string(value)
    if value_type is ValueType.ARRAY:
        element_type, items = value
        body = b"".join(_value(element_type, item) for item in items)
        return struct.pack("<IQ", element_type, len(items)) + body
    return struct.pack("<" + _FORMATS[value_type], value)


def gguf_bytes(
    pairs: list[tuple[str, ValueType, Any]], *, version: int = 3, tensor_count: int = 0
) -> bytes:
    """Encode key/value pairs as a GGUF prefix. Arrays are given as (element_type, items)."""
    out = b"GGUF" + struct.pack("<IQQ", version, tensor_count, len(pairs))
    for key, value_type, value in pairs:
        out += _string(key) + struct.pack("<I", value_type) + _value(value_type, value)
    return out


def ornith_pairs(
    size: str, *, name: str = "", nextn: bool = True
) -> list[tuple[str, ValueType, Any]]:
    """Architecture keys of an Ornith size ("9B", "35B", "122B", "397B"), then a tokenizer key."""
    u32, f32 = ValueType.UINT32, ValueType.FLOAT32
    if size == "9B":
        arch, blocks, width, kv, experts, inner, rank = "qwen35", 32, 4096, 4, 0, 4096, 32
    elif size == "35B":
        arch, blocks, width, kv, experts, inner, rank = "qwen35moe", 40, 2048, 2, 256, 4096, 32
    elif size == "122B":
        arch, blocks, width, kv, experts, inner, rank = "qwen35moe", 48, 3072, 2, 256, 8192, 64
    elif size == "397B":
        arch, blocks, width, kv, experts, inner, rank = "qwen35moe", 60, 4096, 2, 512, 8192, 64
    else:
        raise ValueError(size)
    pairs: list[tuple[str, ValueType, Any]] = [("general.architecture", ValueType.STRING, arch)]
    if name:
        pairs.append(("general.name", ValueType.STRING, name))
    pairs += [
        (f"{arch}.block_count", u32, blocks + (1 if nextn else 0)),
        (f"{arch}.context_length", u32, 262144),
        (f"{arch}.embedding_length", u32, width),
        (f"{arch}.attention.head_count_kv", u32, kv),
        (f"{arch}.rope.freq_base", f32, 10000000.0),
        (f"{arch}.rope.dimension_sections", ValueType.ARRAY, (ValueType.INT32, [11, 11, 10, 0])),
        (f"{arch}.attention.key_length", u32, 256),
        (f"{arch}.attention.value_length", u32, 256),
        (f"{arch}.ssm.conv_kernel", u32, 4),
        (f"{arch}.ssm.state_size", u32, 128),
        (f"{arch}.ssm.group_count", u32, 16),
        (f"{arch}.ssm.time_step_rank", u32, rank),
        (f"{arch}.ssm.inner_size", u32, inner),
        (f"{arch}.full_attention_interval", u32, 4),
    ]
    if experts:
        pairs.append((f"{arch}.expert_count", u32, experts))
    if nextn:
        pairs.append((f"{arch}.nextn_predict_layers", u32, 1))
    pairs.append(("tokenizer.ggml.model", ValueType.STRING, "gpt2"))
    return pairs
