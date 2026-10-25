"""The system check: everything detected about the machine, and what stops or limits a launch.

Each problem carries the exact fix. The launcher never runs ``sudo`` itself, so
fixes that need it are printed for the user to run.
"""

from __future__ import annotations

import enum
import sys
from dataclasses import dataclass

from ornith_launcher.pins import Pins
from ornith_launcher.system.docker import (
    INSTALL_URL,
    NVIDIA_TOOLKIT_URL,
    DockerStatus,
    Flavour,
    choose_flavour,
    detect_docker,
)
from ornith_launcher.system.gpu import Gpu, detect_gpus, has_kfd, nvidia_without_driver
from ornith_launcher.system.host import Host
from ornith_launcher.system.memory import MemoryInfo, Pool, build_pools, detect_memory
from ornith_launcher.system.platform import PlatformInfo, detect_platform


class Severity(str, enum.Enum):
    BLOCKING = "blocking"  # setup cannot continue
    WARNING = "warning"  # setup can continue after the user agrees


@dataclass(frozen=True)
class Problem:
    severity: Severity
    message: str
    fix: str


@dataclass(frozen=True)
class SystemReport:
    platform: PlatformInfo
    memory: MemoryInfo | None
    gpus: tuple[Gpu, ...]
    pools: tuple[Pool, ...]
    docker: DockerStatus
    flavour: Flavour | None
    problems: tuple[Problem, ...]
    python: str

    @property
    def blocking(self) -> tuple[Problem, ...]:
        return tuple(p for p in self.problems if p.severity is Severity.BLOCKING)

    @property
    def warnings(self) -> tuple[Problem, ...]:
        return tuple(p for p in self.problems if p.severity is Severity.WARNING)


def check_system(
    host: Host, pins: Pins, *, system: str | None = None, machine: str | None = None
) -> SystemReport:
    platform = detect_platform(host, system, machine)
    docker = detect_docker(host)
    memory = detect_memory(host, docker_limit=docker.mem_total)
    gpus = tuple(detect_gpus(host))
    pools = build_pools(memory, gpus) if memory is not None else ()
    try:
        flavour = choose_flavour(gpus, platform.arch, pins, kfd=has_kfd(host))
    except LookupError:
        flavour = None
    problems = find_problems(
        platform, memory, gpus, docker, flavour, nvidia_without_driver(host, list(gpus))
    )
    python = ".".join(str(part) for part in sys.version_info[:3])
    return SystemReport(platform, memory, gpus, pools, docker, flavour, problems, python)


def find_problems(
    platform: PlatformInfo,
    memory: MemoryInfo | None,
    gpus: tuple[Gpu, ...],
    docker: DockerStatus,
    flavour: Flavour | None,
    nvidia_card_without_driver: bool = False,
) -> tuple[Problem, ...]:
    problems: list[Problem] = []

    def block(message: str, fix: str) -> None:
        problems.append(Problem(Severity.BLOCKING, message, fix))

    def warn(message: str, fix: str) -> None:
        problems.append(Problem(Severity.WARNING, message, fix))

    if platform.os != "linux":
        block(
            f"{platform.os} is not supported yet; this version runs on Linux only.",
            "Use a Linux machine. Windows and macOS support is planned.",
        )
    if memory is None:
        block("Could not read /proc/meminfo.", "Run ornith-launcher on Linux with /proc mounted.")
    if flavour is None:
        block(
            f"No pinned llama.cpp image supports the {platform.arch} CPU architecture.",
            "Use an amd64 or arm64 machine.",
        )

    if not docker.installed:
        block("Docker is not installed.", f"Install Docker Engine: {INSTALL_URL}")
    elif docker.permission_denied:
        block(
            "Docker is installed, but this user may not use it (permission denied).",
            "Run: sudo usermod -aG docker $USER\nThen log out and back in (or reboot).",
        )
    elif not docker.daemon_running:
        block(
            f"Docker is installed but not running ({docker.error or 'no answer'}).",
            "Run: sudo systemctl start docker",
        )
    if docker.snap:
        warn(
            "Docker is installed as a snap, which can only read files under your home folder.",
            f"Keep models under your home folder, or install Docker Engine from apt: {INSTALL_URL}",
        )

    nvidia = [gpu for gpu in gpus if gpu.vendor == "nvidia"]
    if nvidia and docker.usable and not docker.nvidia_toolkit:
        block(
            "An NVIDIA GPU was found, but Docker cannot use it: "
            "the NVIDIA Container Toolkit is missing.",
            f"Install the NVIDIA Container Toolkit: {NVIDIA_TOOLKIT_URL}\n"
            "Then run: sudo nvidia-ctk runtime configure --runtime=docker && "
            "sudo systemctl restart docker",
        )
    if nvidia_card_without_driver:
        warn(
            "An NVIDIA GPU is present, but nvidia-smi is missing or failing, "
            "so the model would run on the CPU only.",
            "Install the NVIDIA driver for your distribution, reboot, and check with: nvidia-smi",
        )
    elif not gpus:
        warn(
            "No GPU found: the model will run on the CPU only, which is much slower.",
            "Continue to use the CPU, or run ornith-launcher on a machine with a GPU.",
        )
    if flavour is not None and not flavour.image.tested and docker.usable:
        warn(
            f"The {flavour.image.flavour} image has not been tested by the project yet.",
            "Continue; please report problems at "
            "https://github.com/ShourieG/ornith-launcher/issues",
        )
    if memory is not None and memory.docker_limit and memory.docker_limit < memory.total * 0.9:
        warn(
            "Docker may use less memory than this machine has "
            f"({memory.docker_limit / 1024**3:.0f} of {memory.total / 1024**3:.0f} GiB), "
            "probably because it runs in a VM (Docker Desktop).",
            "Raise the memory limit in Docker Desktop's settings, or use Docker Engine.",
        )
    return tuple(problems)
