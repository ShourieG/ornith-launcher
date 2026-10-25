"""GPU temperature, throttle margin, power and load, where the system exposes them."""

from __future__ import annotations

from dataclasses import dataclass

from ornith_launcher.system.host import Host


@dataclass(frozen=True)
class Thermal:
    temperature: float | None  # degrees C
    margin: float | None = None  # degrees C left before throttling (NVIDIA; noisy on GB10)
    power: float | None = None  # watts
    utilisation: float | None = None  # percent


def read_thermal(host: Host) -> Thermal | None:
    if host.which("nvidia-smi"):
        result = host.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,temperature.gpu.tlimit,power.draw,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            timeout=5,
        )
        if result is not None and result.ok and result.stdout.strip():
            values = [_number(v) for v in result.stdout.splitlines()[0].split(",")]
            values += [None] * (4 - len(values))
            return Thermal(*values[:4])
    temperatures = []
    for path in host.glob("/sys/class/drm/card*/device/hwmon/hwmon*/temp1_input") or host.glob(
        "/sys/class/thermal/thermal_zone*/temp"
    ):
        value = _number(host.read_text(path) or "")
        if value is not None:
            temperatures.append(value / 1000)
    return Thermal(max(temperatures)) if temperatures else None


def _number(text: str) -> float | None:
    try:
        return float(text.strip())
    except ValueError:
        return None
