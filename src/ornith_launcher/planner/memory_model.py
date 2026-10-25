"""Estimate how much memory a llama.cpp server needs for one model and its settings.

The server runs with ``-np <slots> --kv-unified -c <context>``: one shared KV pool
of ``context`` tokens plus a small recurrent state per slot. The formulas follow
llama.cpp's own ``fit-params`` estimate, and the constants it does not cover
(compute buffers, runtime overhead) come from measurements in
``data/calibration.json``. All sizes are in bytes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ornith_launcher.catalog.arch import ModelArch
from ornith_launcher.errors import DataFileError
from ornith_launcher.resources import load_json

CALIBRATION_FILE = "calibration.json"

KIB = 1024
MIB = 1024**2
GIB = 1024**3

# Bytes per block and elements per block for each KV cache type (ggml block layouts).
KV_TYPES = {
    "f16": (2, 1),
    "bf16": (2, 1),
    "q8_0": (34, 32),
    "q4_0": (18, 32),
}

# The recurrent state is kept in f32.
_STATE_BYTES = 4

# The launcher passes --cache-ram explicitly so the host prompt cache stays bounded.
DEFAULT_CACHE_RAM_BYTES = 4096 * MIB


@dataclass(frozen=True)
class Calibration:
    """Measured constants; see ``data/calibration.json`` for where each one comes from."""

    compute_base: int
    compute_per_token: float
    overhead_base: int
    prompt_cache_share_of_kv: float
    cpu_overhead_base: int = 1024 * 1024**2
    moe_margin: float = 0.02

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Calibration:
        """Build and validate from the parsed JSON; raises DataFileError on bad data."""
        try:
            compute = data["compute"]
            overhead = data["runtime_overhead"]
            moe = data["moe_offload"]
            cpu = data["cpu_overhead"]
            calibration = cls(
                compute_base=int(compute["base_mib"]) * MIB,
                compute_per_token=float(compute["per_token_kib"]) * KIB,
                overhead_base=int(overhead["base_mib"]) * MIB,
                prompt_cache_share_of_kv=float(overhead["prompt_cache_share_of_kv"]),
                cpu_overhead_base=int(cpu["base_mib"]) * MIB,
                moe_margin=float(moe["margin"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DataFileError(f"{CALIBRATION_FILE} is malformed: {exc!r}") from exc
        if min(calibration.compute_base, calibration.compute_per_token) < 0:
            raise DataFileError(f"{CALIBRATION_FILE} has a negative compute constant")
        if min(calibration.overhead_base, calibration.cpu_overhead_base) < 0:
            raise DataFileError(f"{CALIBRATION_FILE} has a negative overhead constant")
        for name in ("prompt_cache_share_of_kv", "moe_margin"):
            if not 0 <= getattr(calibration, name) <= 1:
                raise DataFileError(f"{CALIBRATION_FILE}: {name} must be 0 to 1")
        return calibration


def load_calibration() -> Calibration:
    """Load the calibration shipped with the package."""
    return Calibration.from_dict(load_json(CALIBRATION_FILE))


@dataclass(frozen=True)
class MemoryEstimate:
    """Bytes needed by each part of a running server."""

    weights: int
    mmproj: int
    kv_cache: int
    recurrent: int
    compute: int
    overhead: int

    @property
    def total(self) -> int:
        return self.weights + self.mmproj + self.context_part

    @property
    def context_part(self) -> int:
        """Memory that grows with the context and slots (all but weights and projector)."""
        return self.kv_cache + self.recurrent + self.compute + self.overhead


def kv_cache_bytes(arch: ModelArch, context: int, kv_type: str = "f16") -> int:
    """KV cache for ``context`` tokens; only the full-attention layers have one."""
    block_bytes, block_size = _kv_type(kv_type)
    elements = context * arch.attention_layers * arch.head_count_kv
    elements *= arch.key_length + arch.value_length
    return -(-elements * block_bytes // block_size)


def kv_bytes_per_token(arch: ModelArch, kv_type: str = "f16") -> float:
    block_bytes, block_size = _kv_type(kv_type)
    per_token = arch.attention_layers * arch.head_count_kv * (arch.key_length + arch.value_length)
    return per_token * block_bytes / block_size


def recurrent_state_bytes(arch: ModelArch, slots: int) -> int:
    """Fixed-size state of the linear-attention (SSM) layers, one copy per slot."""
    linear_layers = arch.main_layers - arch.attention_layers
    if linear_layers <= 0 or not arch.ssm_state_size:
        return 0
    # v_heads x state_size x (inner_size / v_heads) is state_size x inner_size.
    ssm = arch.ssm_state_size * arch.ssm_inner_size
    conv = (arch.ssm_conv_kernel - 1) * (
        2 * arch.ssm_group_count * arch.ssm_state_size + arch.ssm_inner_size
    )
    return slots * linear_layers * (ssm + conv) * _STATE_BYTES


def compute_buffer_bytes(context: int, calibration: Calibration) -> int:
    return calibration.compute_base + round(calibration.compute_per_token * context)


def runtime_overhead_bytes(
    kv_bytes: int, cache_ram: int, calibration: Calibration, gpu: bool = True
) -> int:
    """Driver context, extra slots and the host prompt cache: what llama.cpp's estimate omits."""
    return overhead_base(calibration, gpu) + prompt_cache_bytes(kv_bytes, cache_ram, calibration)


def overhead_base(calibration: Calibration, gpu: bool = True) -> int:
    return calibration.overhead_base if gpu else calibration.cpu_overhead_base


def prompt_cache_bytes(kv_bytes: int, cache_ram: int, calibration: Calibration) -> int:
    """The host prompt cache (``--cache-ram``) grows with use, up to its limit."""
    return min(cache_ram, round(calibration.prompt_cache_share_of_kv * kv_bytes))


def estimate_memory(
    arch: ModelArch,
    *,
    weights: int,
    context: int,
    calibration: Calibration,
    slots: int = 4,
    kv_type: str = "f16",
    mmproj: int = 0,
    cache_ram: int = DEFAULT_CACHE_RAM_BYTES,
    gpu: bool = True,
) -> MemoryEstimate:
    """Estimate the memory a server needs.

    ``weights`` is the GGUF file size (all split parts) and ``mmproj`` the vision
    projector's file size, or 0 when vision is off.
    """
    if context <= 0 or slots <= 0:
        raise ValueError("context and slots must be positive")
    kv = kv_cache_bytes(arch, context, kv_type)
    return MemoryEstimate(
        weights=weights,
        mmproj=mmproj,
        kv_cache=kv,
        recurrent=recurrent_state_bytes(arch, slots),
        compute=compute_buffer_bytes(context, calibration),
        overhead=runtime_overhead_bytes(kv, cache_ram, calibration, gpu),
    )


def _kv_type(kv_type: str) -> tuple[int, int]:
    try:
        return KV_TYPES[kv_type]
    except KeyError:
        raise ValueError(f"unknown KV cache type {kv_type!r}") from None
