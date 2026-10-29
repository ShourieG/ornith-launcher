"""macOS: memory, the GPU's Metal limit and the OS version, read with the system's own tools.

macOS has no ``/proc``, so memory comes from ``sysctl`` and ``vm_stat``. Apple
Silicon shares its memory between the CPU and the GPU, but Metal lets the GPU use
only part of it (the working-set limit). The planner sizes the unified pool from
that limit, read from the native ``llama-server --list-devices`` once the runtime
is installed, and estimated from the total before that.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ornith_launcher.system.gpu import Gpu
from ornith_launcher.system.host import Host
from ornith_launcher.system.memory import MemoryInfo, Pool, PoolKind, baseline

MIB = 1024**2
GIB = 1024**3
MIN_VERSION = (13, 3)  # the pinned llama.cpp build needs macOS 13.3 (its LC_BUILD_VERSION)
# Without a reading from Metal: the share of memory macOS is reported to give the GPU
# (about two thirds up to 36 GiB, three quarters above). ESTIMATED until checked on a Mac.
SMALL_MAC = 36 * GIB
_PAGE_SIZE = re.compile(r"page size of (\d+) bytes")
_DEVICE = re.compile(r"^\s*(\S+): (.+) \((\d+) MiB, (\d+) MiB free\)\s*$")
_SWAP = re.compile(r"(total|used|free) = ([0-9.]+)([KMG])")


@dataclass(frozen=True)
class Device:
    """One line of ``llama-server --list-devices``, e.g. ``MTL0: Apple M2 Max (49152 MiB, ...)``."""

    name: str
    description: str
    total_mib: int
    free_mib: int


def parse_vm_stat(text: str) -> dict[str, int]:
    """``vm_stat`` as bytes per counter ("Pages free" -> "free"), using its page size."""
    match = _PAGE_SIZE.search(text)
    page = int(match.group(1)) if match else 4096
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        rest = rest.strip().rstrip(".")
        if key.startswith("Pages ") and rest.isdigit():
            values[key[len("Pages ") :].strip()] = int(rest) * page
    return values


def available_from_vm_stat(values: Mapping[str, int]) -> int:
    """Free plus inactive pages: what can be used without paging (the psutil rule)."""
    return values.get("free", 0) + values.get("inactive", 0)


def parse_swapusage(text: str) -> tuple[int, int]:
    """(total, free) bytes from ``sysctl -n vm.swapusage``."""
    factor = {"K": 1024, "M": MIB, "G": GIB}
    found = {key: float(value) * factor[unit] for key, value, unit in _SWAP.findall(text)}
    return int(found.get("total", 0)), int(found.get("free", 0))


def parse_version(text: str) -> tuple[int, ...]:
    parts = []
    for part in text.strip().split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts)


def parse_list_devices(text: str) -> list[Device]:
    devices = []
    for line in text.splitlines():
        match = _DEVICE.match(line)
        if match:
            name, description, total, free = match.groups()
            devices.append(Device(name, description, int(total), int(free)))
    return devices


def estimated_limit(total: int) -> int:
    return total * 2 // 3 if total <= SMALL_MAC else total * 3 // 4


def _sysctl(host: Host, name: str) -> str | None:
    result = host.run(["sysctl", "-n", name])
    if result is None or not result.ok:
        return None
    return result.stdout.strip() or None


def detect_memory(host: Host) -> MemoryInfo | None:
    total = _sysctl(host, "hw.memsize")
    if total is None or not total.isdigit():
        return None
    vm = host.run(["vm_stat"])
    values = parse_vm_stat(vm.stdout) if vm is not None and vm.ok else {}
    swap_total, swap_free = parse_swapusage(_sysctl(host, "vm.swapusage") or "")
    return MemoryInfo(
        total=int(total),
        available=available_from_vm_stat(values) if values else int(total),
        swap_total=swap_total,
        swap_free=swap_free,
    )


def meminfo_values(host: Host) -> dict[str, int]:
    """The ``/proc/meminfo`` keys the monitor and status read, from macOS's own tools."""
    memory = detect_memory(host)
    if memory is None:
        return {}
    return {
        "MemTotal": memory.total,
        "MemAvailable": memory.available,
        "SwapTotal": memory.swap_total,
        "SwapFree": memory.swap_free,
    }


def macos_version(host: Host) -> tuple[int, ...] | None:
    result = host.run(["sw_vers", "-productVersion"])
    if result is None or not result.ok:
        return None
    return parse_version(result.stdout) or None


def list_devices(host: Host, server: Path | None) -> list[Device]:
    """The GPUs the native llama-server sees; empty before the runtime is installed."""
    if server is None or not server.is_file():
        return []
    result = host.run([str(server), "--list-devices"], timeout=30)
    if result is None or not result.ok:
        return []
    return parse_list_devices(result.stdout + result.stderr)


def detect_gpus(host: Host, arch: str, devices: Sequence[Device] = ()) -> list[Gpu]:
    """Apple Silicon has one GPU sharing system memory; Intel Macs run on the CPU only."""
    if arch != "arm64":
        return []
    chip = _sysctl(host, "machdep.cpu.brand_string") or "Apple Silicon"
    metal = next((d for d in devices if d.name.startswith("MTL")), None)
    name = metal.description if metal is not None else chip
    return [Gpu(vendor="apple", name=name, index=0, memory_total=None, unified=True)]


def build_pools(
    memory: MemoryInfo, gpus: Sequence[Gpu], devices: Sequence[Device] = ()
) -> tuple[Pool, ...]:
    """One unified pool, the size of Metal's limit, or a CPU-only RAM pool on an Intel Mac."""
    in_use = max(0, memory.total - memory.available)
    if not gpus:
        name = "System RAM (CPU only)"
        return (
            Pool(
                PoolKind.RAM, name, memory.total, baseline(memory.total, in_use), memory.available
            ),
        )
    metal = next((d for d in devices if d.name.startswith("MTL")), None)
    if metal is not None:
        limit = min(memory.total, metal.total_mib * MIB)
        note = f"Metal limit {limit / GIB:.0f} GiB"
    else:
        limit = estimated_limit(memory.total)
        note = f"Metal limit about {limit / GIB:.0f} GiB, ESTIMATED"
    return (
        Pool(
            kind=PoolKind.UNIFIED,
            name=f"Unified memory ({gpus[0].name}; {note})",
            total=limit,
            baseline=baseline(limit, in_use),
            available_now=min(memory.available, limit),
        ),
    )
