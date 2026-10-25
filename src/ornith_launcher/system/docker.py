"""Docker: is it installed and usable, can it reach the GPU, and which image flavour fits.

On Linux the launcher runs llama.cpp in Docker. Each check here comes with the
exact fix, because a missing group membership or toolkit is the most common
reason a first launch fails.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from ornith_launcher.pins import DockerImage, Pins
from ornith_launcher.system.gpu import Gpu
from ornith_launcher.system.host import Host

INSTALL_URL = "https://docs.docker.com/engine/install/"
NVIDIA_TOOLKIT_URL = (
    "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
)

# The NVIDIA Container Toolkit installs one of these; `docker run --gpus` needs it.
_NVIDIA_TOOLKIT_BINARIES = (
    "nvidia-container-runtime-hook",
    "nvidia-ctk",
    "nvidia-container-runtime",
)

# First driver series whose CUDA version the cuda13 image needs.
CUDA13_MIN_DRIVER = 580


@dataclass(frozen=True)
class DockerStatus:
    installed: bool
    snap: bool = False  # the snap package can only see the home folder
    daemon_running: bool = False
    permission_denied: bool = False
    error: str | None = None  # first line of `docker info`'s error, if it failed
    server_version: str | None = None
    mem_total: int | None = None
    root_dir: str | None = None
    rootless: bool = False
    selinux: bool = False
    nvidia_toolkit: bool = False

    @property
    def usable(self) -> bool:
        return self.installed and self.daemon_running


@dataclass(frozen=True)
class Flavour:
    image: DockerImage
    device_args: tuple[str, ...]  # `docker run` flags that give the container the GPU
    reason: str


def detect_docker(host: Host) -> DockerStatus:
    path = host.which("docker")
    if path is None:
        return DockerStatus(installed=False)
    snap = host.realpath(path).startswith("/snap/") or path.startswith("/snap/")
    nvidia_toolkit = any(host.which(name) for name in _NVIDIA_TOOLKIT_BINARIES)
    result = host.run(["docker", "info", "--format", "{{json .}}"], timeout=20)
    if result is None:
        return DockerStatus(
            installed=True, snap=snap, error="docker info did not answer within 20 seconds"
        )
    if not result.ok:
        error = (result.stderr.strip() or result.stdout.strip()).splitlines()
        message = error[-1] if error else f"docker info exited with {result.returncode}"
        return DockerStatus(
            installed=True,
            snap=snap,
            permission_denied="permission denied" in message.lower(),
            error=message,
            nvidia_toolkit=nvidia_toolkit,
        )
    return parse_docker_info(result.stdout, snap=snap, nvidia_toolkit=nvidia_toolkit, host=host)


def parse_docker_info(
    text: str, *, snap: bool = False, nvidia_toolkit: bool = False, host: Host | None = None
) -> DockerStatus:
    try:
        info = json.loads(text)
    except json.JSONDecodeError:
        return DockerStatus(installed=True, snap=snap, error="docker info returned unreadable JSON")
    security = " ".join(info.get("SecurityOptions") or [])
    runtimes = info.get("Runtimes") or {}
    if "nvidia" in runtimes:
        nvidia_toolkit = True
    if not nvidia_toolkit and host is not None:
        # Newer toolkits register the GPU through a CDI spec instead of a runtime.
        for spec_dir in info.get("CDISpecDirs") or []:
            if any("nvidia" in path for path in host.glob(f"{spec_dir}/*")):
                nvidia_toolkit = True
    mem_total = info.get("MemTotal")
    return DockerStatus(
        installed=True,
        snap=snap,
        daemon_running=True,
        server_version=info.get("ServerVersion"),
        mem_total=int(mem_total) if isinstance(mem_total, int) and mem_total > 0 else None,
        root_dir=info.get("DockerRootDir"),
        rootless="rootless" in security,
        selinux="selinux" in security,
        nvidia_toolkit=nvidia_toolkit,
    )


def choose_flavour(gpus: Sequence[Gpu], arch: str, pins: Pins, *, kfd: bool) -> Flavour:
    """Pick the image flavour and device flags for this machine (design 10.2)."""
    images = pins.images

    def pick(name: str, device_args: Sequence[str], reason: str) -> Flavour | None:
        image = images.get(name)
        if image is None or not image.supports(arch):
            return None
        return Flavour(image, tuple(device_args), reason)

    nvidia = [gpu for gpu in gpus if gpu.vendor == "nvidia"]
    if nvidia:
        major = nvidia[0].driver_major
        if major is not None and major >= CUDA13_MIN_DRIVER:
            found = pick("cuda13", ["--gpus", "all"], f"NVIDIA driver {major} supports CUDA 13")
        else:
            found = pick("cuda", ["--gpus", "all"], "NVIDIA driver older than 580: CUDA 12 image")
        if found:
            return found

    if any(gpu.vendor == "amd" for gpu in gpus):
        if kfd:
            found = pick(
                "rocm",
                ["--device", "/dev/kfd", "--device", "/dev/dri", "--group-add", "video"],
                "AMD GPU with ROCm (/dev/kfd)",
            )
            if found:
                return found
        found = pick("vulkan", ["--device", "/dev/dri"], "AMD GPU through Vulkan")
        if found:
            return found

    if any(gpu.vendor == "intel" for gpu in gpus):
        found = pick("intel", ["--device", "/dev/dri"], "Intel GPU (SYCL image)") or pick(
            "vulkan", ["--device", "/dev/dri"], "Intel GPU through Vulkan"
        )
        if found:
            return found

    if any(gpu.vendor == "other" for gpu in gpus):
        found = pick("vulkan", ["--device", "/dev/dri"], "GPU through Vulkan")
        if found:
            return found

    found = pick("cpu", [], "no supported GPU found: CPU only")
    if found is None:
        raise LookupError(f"no pinned llama.cpp image supports linux/{arch}")
    return found
