"""Read the metadata (key/value) section of a GGUF file.

Layout, all little-endian: magic ``GGUF``, version (u32), tensor count (u64),
metadata count (u64), then that many key/value pairs, then tensor infos and data.
Only the key/value pairs are parsed here; they hold everything the catalog needs.

The parser works on a byte prefix of the file, so the caller can fetch just the
start of a multi-GB file (from disk or with an HTTP range request) and fetch more
when :class:`NeedMoreBytes` says the prefix was too short.
"""

from __future__ import annotations

import enum
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAGIC = b"GGUF"
SUPPORTED_VERSIONS = (2, 3)

# Arrays longer than this are skipped and summarised; the token list alone has ~250k entries.
MAX_KEPT_ARRAY = 64

# Sanity limits: a length beyond these means a corrupt file, not a short read.
MAX_STRING_BYTES = 64 * 1024**2
MAX_ARRAY_LENGTH = 2**31

# Prefix sizes tried by read_metadata(). Architecture keys normally fit in the first 256 KiB.
DEFAULT_READ_SIZES = (256 * 1024, 1024**2, 4 * 1024**2, 16 * 1024**2)


class ValueType(enum.IntEnum):
    UINT8 = 0
    INT8 = 1
    UINT16 = 2
    INT16 = 3
    UINT32 = 4
    INT32 = 5
    FLOAT32 = 6
    BOOL = 7
    STRING = 8
    ARRAY = 9
    UINT64 = 10
    INT64 = 11
    FLOAT64 = 12


_SCALAR_FORMATS = {
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


class GGUFError(ValueError):
    """The data is not a GGUF file this parser can read."""


class NeedMoreBytes(Exception):
    """The data ended before parsing finished; ``needed`` is a lower bound on the bytes required."""

    def __init__(self, needed: int) -> None:
        super().__init__(f"GGUF data ends early: at least {needed} bytes are needed")
        self.needed = needed


@dataclass(frozen=True)
class ArraySummary:
    """A long array that was skipped: only its element type and length are kept."""

    element_type: ValueType
    length: int


@dataclass(frozen=True)
class GGUFMetadata:
    """Parsed key/value pairs of one GGUF file.

    ``complete`` is False when parsing stopped early because the caller's
    ``until`` condition was met; later keys were then not read.
    """

    version: int
    tensor_count: int
    values: Mapping[str, Any]
    complete: bool

    @property
    def architecture(self) -> str | None:
        return self.values.get("general.architecture")


class _Reader:
    """Sequential reader over a byte prefix; raises NeedMoreBytes when the prefix runs out."""

    def __init__(self, data: bytes) -> None:
        self._data = memoryview(data)
        self.offset = 0

    def take(self, size: int) -> memoryview:
        end = self.offset + size
        if end > len(self._data):
            raise NeedMoreBytes(end)
        chunk = self._data[self.offset : end]
        self.offset = end
        return chunk

    def skip(self, size: int) -> None:
        self.take(size)

    def unpack(self, fmt: str) -> Any:
        return struct.unpack("<" + fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        length = self.unpack("Q")
        if length > MAX_STRING_BYTES:
            raise GGUFError(f"string of {length} bytes at offset {self.offset}: file looks corrupt")
        return bytes(self.take(length)).decode("utf-8", errors="replace")


def _value_type(raw: int) -> ValueType:
    try:
        return ValueType(raw)
    except ValueError:
        raise GGUFError(f"unknown GGUF value type {raw}") from None


def _read_value(reader: _Reader, value_type: ValueType) -> Any:
    if value_type is ValueType.STRING:
        return reader.string()
    if value_type is ValueType.ARRAY:
        return _read_array(reader)
    return reader.unpack(_SCALAR_FORMATS[value_type])


def _read_array(reader: _Reader) -> tuple[Any, ...] | ArraySummary:
    element_type = _value_type(reader.unpack("I"))
    length = reader.unpack("Q")
    if length > MAX_ARRAY_LENGTH:
        raise GGUFError(f"array of {length} elements at offset {reader.offset}: file looks corrupt")
    if length <= MAX_KEPT_ARRAY:
        return tuple(_read_value(reader, element_type) for _ in range(length))

    # Skip long arrays without building them: fixed-size elements in one jump,
    # strings and nested arrays one element at a time.
    if element_type in _SCALAR_FORMATS:
        reader.skip(length * struct.calcsize(_SCALAR_FORMATS[element_type]))
    else:
        for _ in range(length):
            _read_value(reader, element_type)
    return ArraySummary(element_type, length)


def parse_metadata(
    data: bytes, until: Callable[[Mapping[str, Any]], bool] | None = None
) -> GGUFMetadata:
    """Parse the key/value pairs from a byte prefix of a GGUF file.

    ``until`` is called after each key; returning True stops parsing early, which
    saves fetching the large tokenizer arrays. Raises NeedMoreBytes if the prefix
    ends first, and GGUFError if the data is not a readable GGUF file.
    """
    reader = _Reader(data)
    if bytes(reader.take(len(MAGIC))) != MAGIC:
        raise GGUFError("not a GGUF file (wrong magic bytes)")
    version = reader.unpack("I")
    if version not in SUPPORTED_VERSIONS:
        raise GGUFError(f"GGUF version {version} is not supported (supported: 2, 3)")
    tensor_count = reader.unpack("Q")
    pair_count = reader.unpack("Q")

    values: dict[str, Any] = {}
    for index in range(pair_count):
        key = reader.string()
        values[key] = _read_value(reader, _value_type(reader.unpack("I")))
        if until is not None and until(values):
            return GGUFMetadata(version, tensor_count, values, complete=index == pair_count - 1)
    return GGUFMetadata(version, tensor_count, values, complete=True)


def stop_at_key_prefix(prefix: str) -> Callable[[Mapping[str, Any]], bool]:
    """Return an ``until`` condition that stops at the first key starting with ``prefix``.

    GGUF writers group keys as ``general.*``, then ``<arch>.*``, then ``tokenizer.*``,
    so ``stop_at_key_prefix("tokenizer.")`` reads everything except the tokenizer.
    """

    def until(values: Mapping[str, Any]) -> bool:
        last_key = next(reversed(values.keys()))
        return last_key.startswith(prefix)

    return until


def read_metadata(
    fetch: Callable[[int], bytes],
    until: Callable[[Mapping[str, Any]], bool] | None = None,
    sizes: Sequence[int] = DEFAULT_READ_SIZES,
) -> GGUFMetadata:
    """Parse metadata from a source, asking for a longer prefix each time the last was too short.

    ``fetch(n)`` returns the first ``n`` bytes of the file, or fewer if the file is
    shorter. Works the same for local files and HTTP range requests.
    """
    needed = 0
    for size in sizes:
        if size < needed:
            continue
        data = fetch(size)
        try:
            return parse_metadata(data, until)
        except NeedMoreBytes as exc:
            if len(data) < size:
                raise  # The source returned everything it has: the file itself is truncated.
            needed = exc.needed
    raise NeedMoreBytes(max(needed, sizes[-1] + 1))


def file_prefix_reader(path: Path) -> Callable[[int], bytes]:
    """Return a ``fetch`` function for :func:`read_metadata` that reads from a local file."""

    def fetch(size: int) -> bytes:
        with open(path, "rb") as handle:
            return handle.read(size)

    return fetch
