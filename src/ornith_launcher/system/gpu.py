"""GPUs and their drivers.

NVIDIA GPUs are read from ``nvidia-smi``; AMD, Intel and other GPUs from sysfs
(``/sys/class/drm/card*/device``). A GPU whose memory is shared with the CPU
(DGX Spark / GB10, AMD APUs) is marked ``unified``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ornith_launcher.system.host import Host

MIB = 1024**2
GIB = 1024**3

VENDOR_IDS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}

_NVIDIA_QUERY = "index,name,driver_version,memory.total,memory.used,compute_cap"

# AMD APUs reserve a small VRAM carve-out and use GTT (system RAM) for the rest.
_APU_MAX_VRAM = 4 * GIB


@dataclass(frozen=True)
class Gpu:
    vendor: str  # "nvidia", "amd", "intel" or "other"
    name: str
    index: int
    memory_total: int | None  # dedicated memory in bytes; None when shared or unknown
    memory_used: int | None = None
    driver_version: str | None = None
    compute_cap: str | None = None
    unified: bool = False  # shares system memory with the CPU
    gtt_total: int | None = None  # AMD: system memory the GPU can map

    @property
    def driver_major(self) -> int | None:
        if not self.driver_version:
            return None
        head = self.driver_version.split(".", 1)[0]
        return int(head) if head.isdigit() else None


def parse_nvidia_smi(csv_text: str) -> list[Gpu]:
    """Parse ``nvidia-smi --query-gpu=<_NVIDIA_QUERY> --format=csv,noheader,nounits``."""
    gpus = []
    for line in csv_text.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 6:
            continue
        index, name, driver, total, used, cap = fields
        total_mib = _number(total)
        # GB10 reports "[N/A]" for memory: its GPU uses the system's unified memory.
        unified = total_mib is None or "GB10" in name.upper()
        gpus.append(
            Gpu(
                vendor="nvidia",
                name=name,
                index=int(index) if index.isdigit() else len(gpus),
                memory_total=None if unified or total_mib is None else total_mib * MIB,
                memory_used=None if unified else _mib(used),
                driver_version=driver or None,
                compute_cap=cap if _number(cap) is not None else None,
                unified=unified,
            )
        )
    return gpus


def detect_gpus(host: Host) -> list[Gpu]:
    gpus: list[Gpu] = []
    if host.which("nvidia-smi"):
        result = host.run(
            ["nvidia-smi", f"--query-gpu={_NVIDIA_QUERY}", "--format=csv,noheader,nounits"]
        )
        if result is not None and result.ok:
            gpus.extend(parse_nvidia_smi(result.stdout))
    gpus.extend(_sysfs_gpus(host, skip_nvidia=bool(gpus)))
    return gpus


def nvidia_without_driver(host: Host, gpus: list[Gpu]) -> bool:
    """An NVIDIA card is present but ``nvidia-smi`` is missing or failing."""
    if any(gpu.vendor == "nvidia" for gpu in gpus):
        return False
    return any(_vendor(host, card) == "nvidia" for card in _cards(host))


def has_kfd(host: Host) -> bool:
    """The ROCm compute interface is available (needed for the ROCm image)."""
    return host.exists("/dev/kfd")


def _sysfs_gpus(host: Host, *, skip_nvidia: bool) -> list[Gpu]:
    gpus = []
    for card in _cards(host):
        vendor = _vendor(host, card)
        if vendor is None or vendor == "nvidia":
            # NVIDIA cards come from nvidia-smi; one without a driver is reported separately.
            continue
        device = f"{card}/device"
        vram = _read_int(host, f"{device}/mem_info_vram_total")
        gtt = _read_int(host, f"{device}/mem_info_gtt_total")
        unified = vendor == "amd" and vram is not None and gtt is not None
        unified = unified and vram <= _APU_MAX_VRAM and gtt > vram
        dedicated = vram if vendor == "amd" and not unified else None
        gpus.append(
            Gpu(
                vendor=vendor,
                name=_sysfs_name(host, device, vendor),
                index=len(gpus),
                memory_total=dedicated,
                memory_used=_read_int(host, f"{device}/mem_info_vram_used") if dedicated else None,
                unified=unified,
                gtt_total=gtt if vendor == "amd" else None,
            )
        )
    return gpus


def _cards(host: Host) -> list[str]:
    # card0, card1, ... but not connector entries such as card0-HDMI-A-1.
    return [
        path for path in host.glob("/sys/class/drm/card*") if "-" not in path.rsplit("/", 1)[-1]
    ]


def _vendor(host: Host, card: str) -> str | None:
    raw = host.read_text(f"{card}/device/vendor")
    if raw is None:
        return None
    return VENDOR_IDS.get(raw.strip().lower(), "other")


def _sysfs_name(host: Host, device: str, vendor: str) -> str:
    product = host.read_text(f"{device}/product_name")
    if product and product.strip():
        return product.strip()
    device_id = (host.read_text(f"{device}/device") or "").strip()
    return f"{vendor.upper()} GPU {device_id}".strip()


def _read_int(host: Host, path: str) -> int | None:
    text = host.read_text(path)
    if text is None:
        return None
    text = text.strip()
    return int(text) if text.isdigit() else None


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _mib(text: str) -> int | None:
    value = _number(text)
    return None if value is None else int(value * MIB)
