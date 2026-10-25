"""System memory and the memory pools the planner fits models into.

A pool is memory a model can be placed in: one unified pool on machines whose
GPU shares system memory (or that have no usable GPU), or a VRAM pool per
discrete GPU plus a RAM pool. The planner budgets against a pool's size minus
a baseline for the OS and desktop, not against what happens to be free now,
because other apps can be closed.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass

from ornith_launcher.system.gpu import Gpu
from ornith_launcher.system.host import Host

GIB = 1024**3

# Baseline clamp (design 6.2): at least 2 GiB, at most a quarter of the pool.
MIN_BASELINE = 2 * GIB
MAX_BASELINE_SHARE = 0.25
# A discrete GPU still needs room for its driver context and the desktop.
MIN_VRAM_BASELINE = GIB // 2

# cgroup v1 reports "no limit" as a huge number near 2**63.
_NO_CGROUP_LIMIT = 2**60


class PoolKind(str, enum.Enum):
    UNIFIED = "unified"  # the GPU and CPU share it
    VRAM = "vram"
    RAM = "ram"  # system RAM: next to discrete GPUs, or the only pool on a CPU-only machine


@dataclass(frozen=True)
class MemoryInfo:
    """System memory in bytes, from ``/proc/meminfo`` plus container limits."""

    total: int
    available: int
    swap_total: int = 0
    swap_free: int = 0
    docker_limit: int | None = None  # what `docker info` reports; containers get no more
    cgroup_limit: int | None = None  # set when this process runs inside a limited cgroup

    @property
    def usable_total(self) -> int:
        """The most memory a container can use: total RAM bounded by Docker and cgroup limits."""
        limits = [self.total] + [x for x in (self.docker_limit, self.cgroup_limit) if x]
        return min(limits)

    @property
    def swap_used(self) -> int:
        return max(0, self.swap_total - self.swap_free)


@dataclass(frozen=True)
class Pool:
    kind: PoolKind
    name: str
    total: int
    baseline: int  # memory the OS, desktop and other apps keep using
    available_now: int | None = None
    gpu_index: int | None = None

    @property
    def usable(self) -> int:
        return max(0, self.total - self.baseline)


def parse_meminfo(text: str) -> dict[str, int]:
    """Values of ``/proc/meminfo`` in bytes (the file uses kB = KiB)."""
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            factor = 1024 if len(parts) > 1 and parts[1] == "kB" else 1
            values[key.strip()] = int(parts[0]) * factor
    return values


def parse_cgroup_limit(text: str | None) -> int | None:
    """Parse a cgroup ``memory.max`` (v2) or ``memory.limit_in_bytes`` (v1) value."""
    if text is None:
        return None
    text = text.strip()
    if not text.isdigit():
        return None  # "max" means no limit
    value = int(text)
    return value if value < _NO_CGROUP_LIMIT else None


def detect_memory(host: Host, docker_limit: int | None = None) -> MemoryInfo | None:
    text = host.read_text("/proc/meminfo")
    if text is None:
        return None
    values = parse_meminfo(text)
    if "MemTotal" not in values:
        return None
    return MemoryInfo(
        total=values["MemTotal"],
        available=values.get("MemAvailable", values.get("MemFree", 0)),
        swap_total=values.get("SwapTotal", 0),
        swap_free=values.get("SwapFree", 0),
        docker_limit=docker_limit,
        cgroup_limit=_cgroup_limit(host),
    )


def _cgroup_limit(host: Host) -> int | None:
    # cgroup v2: "0::/some/path" in /proc/self/cgroup, limit in that folder's memory.max.
    for line in (host.read_text("/proc/self/cgroup") or "").splitlines():
        if line.startswith("0::"):
            path = line[3:].strip().rstrip("/")
            return parse_cgroup_limit(host.read_text(f"/sys/fs/cgroup{path}/memory.max"))
    return parse_cgroup_limit(host.read_text("/sys/fs/cgroup/memory/memory.limit_in_bytes"))


def baseline(total: int, in_use: int, minimum: int = MIN_BASELINE) -> int:
    """Memory in use at scan time, clamped to [minimum, 25% of total]."""
    return max(minimum, min(in_use, int(total * MAX_BASELINE_SHARE)))


def build_pools(memory: MemoryInfo, gpus: Iterable[Gpu]) -> tuple[Pool, ...]:
    """Turn system memory and GPUs into the pools the planner places models in."""
    gpus = list(gpus)
    ram_total = memory.usable_total
    ram_in_use = max(0, memory.total - memory.available)
    ram_baseline = baseline(ram_total, ram_in_use)
    discrete = [gpu for gpu in gpus if not gpu.unified and gpu.memory_total]
    unified = [gpu for gpu in gpus if gpu.unified]

    if discrete:
        pools = [
            Pool(
                kind=PoolKind.VRAM,
                name=f"{gpu.name} (GPU {gpu.index})",
                total=gpu.memory_total,
                baseline=baseline(gpu.memory_total, gpu.memory_used or 0, MIN_VRAM_BASELINE),
                available_now=(
                    gpu.memory_total - gpu.memory_used if gpu.memory_used is not None else None
                ),
                gpu_index=gpu.index,
            )
            for gpu in discrete
            if gpu.memory_total is not None
        ]
        pools.append(_ram_pool(PoolKind.RAM, "System RAM", ram_total, ram_baseline, memory))
        return tuple(pools)

    if unified:
        gpu = unified[0]
        total = ram_total
        if gpu.vendor == "amd" and gpu.gtt_total:
            # APU: the GPU can reach its carve-out plus GTT, bounded by what the system has.
            total = min(ram_total, gpu.gtt_total + (gpu.memory_total or 0))
        name = f"Unified memory ({gpu.name})"
        return (_ram_pool(PoolKind.UNIFIED, name, total, baseline(total, ram_in_use), memory),)

    return (_ram_pool(PoolKind.RAM, "System RAM (CPU only)", ram_total, ram_baseline, memory),)


def _ram_pool(kind: PoolKind, name: str, total: int, base: int, memory: MemoryInfo) -> Pool:
    return Pool(kind=kind, name=name, total=total, baseline=base, available_now=memory.available)
