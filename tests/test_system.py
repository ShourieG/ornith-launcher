from __future__ import annotations

import json
import unittest

from ornith_launcher.pins import load_pins
from ornith_launcher.system import docker as docker_mod
from ornith_launcher.system.docker import DockerStatus, choose_flavour, detect_docker
from ornith_launcher.system.gpu import Gpu, detect_gpus, parse_nvidia_smi
from ornith_launcher.system.memory import (
    GIB,
    MemoryInfo,
    PoolKind,
    baseline,
    build_pools,
    parse_cgroup_limit,
    parse_meminfo,
)
from ornith_launcher.system.platform import detect_platform, normalise_arch
from ornith_launcher.system.report import Severity, check_system, find_problems
from tests.fake_host import FakeHost, failed, ok

MIB = 1024**2

# Recorded on a DGX Spark (GB10) on 2026-10-25.
SPARK_SMI = "0, NVIDIA GB10, 580.178.04, [N/A], [N/A], 12.1\n"
SPARK_MEMINFO = """MemTotal:       127598608 kB
MemFree:        119763144 kB
MemAvailable:   122117160 kB
SwapTotal:      16777212 kB
SwapFree:       16777212 kB
"""
SPARK_DOCKER_INFO = {
    "MemTotal": 130660974592,
    "ServerVersion": "29.6.2",
    "Runtimes": {"runc": {"path": "runc"}},
    "CDISpecDirs": ["/etc/cdi", "/run/cdi"],
    "SecurityOptions": ["name=apparmor", "name=seccomp,profile=builtin", "name=cgroupns"],
    "DockerRootDir": "/var/lib/docker",
}
SMI_ARGS = (
    "nvidia-smi",
    "--query-gpu=index,name,driver_version,memory.total,memory.used,compute_cap",
    "--format=csv,noheader,nounits",
)
DOCKER_INFO_ARGS = ("docker", "info", "--format", "{{json .}}")


def spark_host(**overrides) -> FakeHost:
    settings = dict(
        env={"DISPLAY": ":1", "XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"},
        files={
            "/proc/meminfo": SPARK_MEMINFO,
            "/proc/self/cgroup": "0::/user.slice/user-1000.slice/session-2.scope\n",
            "/sys/class/drm/card1/device/vendor": "0x10de\n",
            "/run/cdi/nvidia.yaml": "cdiVersion: 0.5.0\n",
        },
        commands={SMI_ARGS: ok(SPARK_SMI), DOCKER_INFO_ARGS: ok(json.dumps(SPARK_DOCKER_INFO))},
        programs={"nvidia-smi": "/usr/bin/nvidia-smi", "docker": "/usr/bin/docker"},
    )
    settings.update(overrides)
    return FakeHost(**settings)


def gpu(vendor: str, **fields) -> Gpu:
    fields.setdefault("name", f"{vendor} gpu")
    fields.setdefault("index", 0)
    fields.setdefault("memory_total", None)
    return Gpu(vendor=vendor, **fields)


class PlatformTest(unittest.TestCase):
    def test_desktop_session(self):
        info = detect_platform(spark_host(), "Linux", "aarch64")
        self.assertEqual((info.os, info.arch), ("linux", "arm64"))
        self.assertEqual(info.desktop, "GNOME")
        self.assertFalse(info.headless)

    def test_no_display_is_headless(self):
        info = detect_platform(FakeHost(env={"XDG_SESSION_TYPE": "tty"}), "Linux", "x86_64")
        self.assertTrue(info.headless)
        self.assertEqual(info.arch, "amd64")

    def test_arch_names(self):
        self.assertEqual(normalise_arch("AMD64"), "amd64")
        self.assertEqual(normalise_arch("arm64"), "arm64")
        self.assertEqual(normalise_arch("riscv64"), "riscv64")


class GpuTest(unittest.TestCase):
    def test_gb10_is_unified(self):
        (spark,) = parse_nvidia_smi(SPARK_SMI)
        self.assertTrue(spark.unified)
        self.assertIsNone(spark.memory_total)
        self.assertEqual(spark.driver_major, 580)
        self.assertEqual(spark.compute_cap, "12.1")

    def test_discrete_nvidia(self):
        text = (
            "0, NVIDIA GeForce RTX 4090, 550.54.14, 24564, 812, 8.9\n"
            "1, NVIDIA A100, 550.54.14, 81920, 0, 8.0\n"
        )
        first, second = parse_nvidia_smi(text)
        self.assertFalse(first.unified)
        self.assertEqual(first.memory_total, 24564 * MIB)
        self.assertEqual(first.memory_used, 812 * MIB)
        self.assertEqual(second.index, 1)

    def test_amd_apu_and_discrete_from_sysfs(self):
        host = FakeHost(
            files={
                "/sys/class/drm/card0/device/vendor": "0x1002",
                "/sys/class/drm/card0/device/mem_info_vram_total": str(512 * MIB),
                "/sys/class/drm/card0/device/mem_info_gtt_total": str(64 * GIB),
                "/sys/class/drm/card0-HDMI-A-1/status": "connected",
                "/sys/class/drm/card1/device/vendor": "0x1002",
                "/sys/class/drm/card1/device/mem_info_vram_total": str(24 * GIB),
                "/sys/class/drm/card1/device/mem_info_vram_used": str(GIB),
                "/sys/class/drm/card1/device/mem_info_gtt_total": str(128 * GIB),
                "/sys/class/drm/card1/device/product_name": "Radeon RX 7900 XTX",
            }
        )
        apu, card = detect_gpus(host)
        self.assertTrue(apu.unified)
        self.assertIsNone(apu.memory_total)
        self.assertFalse(card.unified)
        self.assertEqual(card.memory_total, 24 * GIB)
        self.assertEqual(card.name, "Radeon RX 7900 XTX")

    def test_nvidia_card_comes_from_nvidia_smi_only(self):
        self.assertEqual([g.name for g in detect_gpus(spark_host())], ["NVIDIA GB10"])
        no_driver = spark_host(programs={})
        self.assertEqual(detect_gpus(no_driver), [])


class MemoryTest(unittest.TestCase):
    def test_parse_meminfo(self):
        values = parse_meminfo(SPARK_MEMINFO)
        self.assertEqual(values["MemTotal"], 127598608 * 1024)
        self.assertEqual(values["SwapFree"], 16777212 * 1024)

    def test_cgroup_limits(self):
        self.assertIsNone(parse_cgroup_limit("max\n"))
        self.assertIsNone(parse_cgroup_limit(None))
        self.assertIsNone(parse_cgroup_limit("9223372036854771712"))
        self.assertEqual(parse_cgroup_limit("17179869184\n"), 16 * GIB)

    def test_baseline_is_clamped(self):
        self.assertEqual(baseline(128 * GIB, GIB), 2 * GIB)
        self.assertEqual(baseline(128 * GIB, 5 * GIB), 5 * GIB)
        self.assertEqual(baseline(16 * GIB, 12 * GIB), 4 * GIB)

    def test_spark_has_one_unified_pool(self):
        memory = MemoryInfo(total=127598608 * 1024, available=122117160 * 1024)
        (pool,) = build_pools(memory, parse_nvidia_smi(SPARK_SMI))
        self.assertEqual(pool.kind, PoolKind.UNIFIED)
        self.assertEqual(pool.total, memory.total)
        self.assertAlmostEqual(pool.baseline / GIB, 5.2, 1)

    def test_discrete_gpu_gets_vram_and_ram_pools(self):
        memory = MemoryInfo(total=64 * GIB, available=60 * GIB)
        card = gpu("nvidia", memory_total=24 * GIB, memory_used=GIB // 4)
        vram, ram = build_pools(memory, [card])
        self.assertEqual((vram.kind, ram.kind), (PoolKind.VRAM, PoolKind.RAM))
        self.assertEqual(vram.baseline, GIB // 2)
        self.assertEqual(vram.usable, 24 * GIB - GIB // 2)
        self.assertEqual(ram.baseline, 4 * GIB)

    def test_amd_apu_pool_is_bounded_by_ram(self):
        memory = MemoryInfo(total=32 * GIB, available=28 * GIB)
        apu = gpu("amd", unified=True, gtt_total=16 * GIB, memory_total=None)
        (pool,) = build_pools(memory, [apu])
        self.assertEqual(pool.total, 16 * GIB)
        big_gtt = gpu("amd", unified=True, gtt_total=64 * GIB, memory_total=None)
        self.assertEqual(build_pools(memory, [big_gtt])[0].total, 32 * GIB)

    def test_cpu_only_and_container_limits(self):
        memory = MemoryInfo(total=64 * GIB, available=60 * GIB, docker_limit=32 * GIB)
        (pool,) = build_pools(memory, [])
        self.assertEqual(pool.kind, PoolKind.RAM)
        self.assertEqual(pool.total, 32 * GIB)
        limited = MemoryInfo(total=64 * GIB, available=60 * GIB, cgroup_limit=16 * GIB)
        self.assertEqual(build_pools(limited, [])[0].total, 16 * GIB)


class FlavourTest(unittest.TestCase):
    def setUp(self):
        self.pins = load_pins()

    def flavour(self, gpus, arch="amd64", kfd=False):
        return choose_flavour(gpus, arch, self.pins, kfd=kfd)

    def test_table(self):
        nvidia_new = gpu("nvidia", driver_version="580.178.04", unified=True)
        nvidia_old = gpu("nvidia", driver_version="550.54.14", memory_total=GIB)
        cases = [
            ([nvidia_new], "arm64", False, "cuda13", ("--gpus", "all")),
            ([nvidia_old], "amd64", False, "cuda", ("--gpus", "all")),
            ([gpu("amd")], "amd64", True, "rocm", ("--device", "/dev/kfd")),
            ([gpu("amd")], "amd64", False, "vulkan", ("--device", "/dev/dri")),
            ([gpu("amd")], "arm64", True, "vulkan", ("--device", "/dev/dri")),
            ([gpu("intel")], "amd64", False, "intel", ("--device", "/dev/dri")),
            ([gpu("intel")], "arm64", False, "vulkan", ("--device", "/dev/dri")),
            ([gpu("other")], "arm64", False, "vulkan", ("--device", "/dev/dri")),
            ([], "amd64", False, "cpu", ()),
            ([], "s390x", False, "cpu", ()),
        ]
        for gpus, arch, kfd, expected, args in cases:
            with self.subTest(gpus=[g.vendor for g in gpus], arch=arch, kfd=kfd):
                flavour = self.flavour(gpus, arch, kfd)
                self.assertEqual(flavour.image.flavour, expected)
                self.assertEqual(flavour.device_args[: len(args)], args)
                self.assertIn("@sha256:", flavour.image.reference)

    def test_unsupported_arch(self):
        with self.assertRaises(LookupError):
            self.flavour([], "riscv64")


class DockerTest(unittest.TestCase):
    def test_not_installed(self):
        self.assertFalse(detect_docker(FakeHost()).installed)

    def test_spark_toolkit_found_through_binary_or_cdi(self):
        status = detect_docker(spark_host())
        self.assertTrue(status.usable)
        self.assertTrue(status.nvidia_toolkit)  # through /run/cdi/nvidia.yaml
        self.assertEqual(status.mem_total, 130660974592)
        self.assertFalse(status.snap)
        host = spark_host(files={})
        host.programs["nvidia-ctk"] = "/usr/bin/nvidia-ctk"
        self.assertTrue(detect_docker(host).nvidia_toolkit)
        self.assertFalse(detect_docker(spark_host(files={})).nvidia_toolkit)

    def test_permission_denied_and_daemon_down(self):
        denied = failed(
            "permission denied while trying to connect to the Docker daemon socket at "
            "unix:///var/run/docker.sock: Get ...: dial unix /var/run/docker.sock: "
            "connect: permission denied"
        )
        status = detect_docker(spark_host(commands={DOCKER_INFO_ARGS: denied}))
        self.assertTrue(status.permission_denied)
        self.assertFalse(status.usable)
        down = failed(
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
            "Is the docker daemon running?"
        )
        status = detect_docker(spark_host(commands={DOCKER_INFO_ARGS: down}))
        self.assertFalse(status.permission_denied)
        self.assertIn("Cannot connect", status.error)

    def test_snap_and_security_options(self):
        info = dict(SPARK_DOCKER_INFO, SecurityOptions=["name=selinux", "name=rootless"])
        host = spark_host(
            commands={DOCKER_INFO_ARGS: ok(json.dumps(info))},
            programs={"docker": "/usr/bin/docker"},
            links={"/usr/bin/docker": "/snap/docker/2915/bin/docker"},
        )
        status = detect_docker(host)
        self.assertTrue(status.snap and status.selinux and status.rootless)


class ReportTest(unittest.TestCase):
    def test_spark_has_no_blocking_problems(self):
        report = check_system(spark_host(), load_pins(), system="Linux", machine="aarch64")
        self.assertEqual(report.blocking, ())
        self.assertEqual(report.flavour.image.flavour, "cuda13")
        self.assertEqual(len(report.pools), 1)
        self.assertEqual(report.warnings, ())

    def test_missing_toolkit_blocks_with_the_fix(self):
        host = spark_host(files={"/proc/meminfo": SPARK_MEMINFO})
        report = check_system(host, load_pins(), system="Linux", machine="aarch64")
        (problem,) = report.blocking
        self.assertIn("NVIDIA Container Toolkit", problem.message)
        self.assertIn(docker_mod.NVIDIA_TOOLKIT_URL, problem.fix)

    def test_every_problem_has_a_fix(self):
        platform = detect_platform(FakeHost(), "Darwin", "arm64")
        problems = find_problems(
            platform, None, (), DockerStatus(installed=True, permission_denied=True), None
        )
        severities = {p.severity for p in problems}
        self.assertEqual(severities, {Severity.BLOCKING, Severity.WARNING})
        for problem in problems:
            with self.subTest(problem.message):
                self.assertTrue(problem.fix)
        self.assertTrue(any("usermod -aG docker" in p.fix for p in problems))

    def test_nvidia_card_without_driver_warns(self):
        host = spark_host(programs={"docker": "/usr/bin/docker"})
        report = check_system(host, load_pins(), system="Linux", machine="aarch64")
        self.assertTrue(any("nvidia-smi" in p.message for p in report.warnings))
        self.assertEqual(report.flavour.image.flavour, "cpu")


if __name__ == "__main__":
    unittest.main()
