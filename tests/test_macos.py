"""macOS support, tested on any OS: detection, the native backend, terminals, icons, uninstall.

Every Mac command (sysctl, vm_stat, sw_vers, ps, open) is answered by a fake host,
so these tests run on Linux CI as well. They do not replace a run on a real Mac.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import itertools
import os
import re
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ornith_launcher import cli, launch
from ornith_launcher.app import App
from ornith_launcher.bench.perplexity import native_perplexity_args
from ornith_launcher.config import read_json, resolve_paths
from ornith_launcher.download import Status
from ornith_launcher.errors import DataFileError
from ornith_launcher.installed import load_installed, record
from ornith_launcher.integrations.terminals import (
    APPLESCRIPT_TIMEOUT,
    command_script,
    current_terminal,
    detect_terminals,
    iterm_script,
    open_in_terminal,
    runs_in,
)
from ornith_launcher.pins import NativeAsset, NativeBuild, Pins, load_pins
from ornith_launcher.profiles import load_profile, save_profile
from ornith_launcher.runtime.api import ServerError
from ornith_launcher.runtime.docker import DockerRuntime, container_name
from ornith_launcher.runtime.native import (
    QUARANTINE,
    FileFollower,
    NativeRuntime,
    native_config,
    unpack,
)
from ornith_launcher.runtime.server import fit_params_args
from ornith_launcher.system import macos
from ornith_launcher.system.host import CommandResult, LocalHost
from ornith_launcher.system.memory import PoolKind, read_meminfo
from ornith_launcher.system.platform import detect_platform
from ornith_launcher.system.report import Severity, check_system
from tests.fake_host import FakeHost, ok
from tests.runtime_fixtures import FakeHttp, profile

GIB = 1024**3
REFERENCE = "llama-b11552-macos-arm64"
VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                              100000.
Pages active:                            900000.
Pages inactive:                          800000.
Pages speculative:                        50000.
Pages wired down:                        400000.
"""
SWAP = "total = 2048.00M  used = 512.00M  free = 1536.00M  (encrypted)\n"
DEVICES = "Available devices:\n  MTL0: Apple M2 Max (49152 MiB, 49151 MiB free)\n"


def mac_host(memsize: int = 64 * GIB, version: str = "14.5", **kwargs) -> FakeHost:
    host = FakeHost(
        programs={"sysctl": "/usr/sbin/sysctl", "vm_stat": "/usr/bin/vm_stat"},
        **kwargs,
    )
    host.programs["sw_vers"] = "/usr/bin/sw_vers"
    host.commands.update(
        {
            ("sysctl", "-n", "hw.memsize"): ok(f"{memsize}\n"),
            ("vm_stat",): ok(VM_STAT),
            ("sysctl", "-n", "vm.swapusage"): ok(SWAP),
            ("sw_vers", "-productVersion"): ok(f"{version}\n"),
            ("sysctl", "-n", "machdep.cpu.brand_string"): ok("Apple M2 Max\n"),
        }
    )
    return host


def native_profile(root: Path, **runtime):
    settings = dict(image=REFERENCE, flavour="metal", device_args=[], direct_io=False)
    settings.update(runtime)
    return profile(root, **settings)


def install_fake_runtime(paths, reference: str = REFERENCE) -> Path:
    folder = paths.data / "runtime" / reference
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("llama-server", "llama-fit-params", "llama-perplexity"):
        (folder / name).write_text("#!/bin/sh\n")
    return folder


def ticking_clock(step: float = 1.0):
    counter = itertools.count(0.0, step)
    return lambda: next(counter)


class PinsTest(unittest.TestCase):
    def test_macos_builds_are_pinned_by_sha256(self):
        pins = load_pins()
        arm = pins.native["macos-arm64"]
        self.assertEqual(arm.reference, REFERENCE)
        self.assertEqual(arm.assets[0].name, "llama-b11552-bin-macos-arm64.tar.gz")
        self.assertTrue(arm.assets[0].url.startswith("https://github.com/ggml-org/llama.cpp/"))
        self.assertEqual(len(arm.assets[0].sha256), 64)
        self.assertIn("macos-x64", pins.native)

    def test_an_unpinned_archive_is_refused(self):
        data = {
            "llama_cpp": {"build": "b1"},
            "docker": {"image": "x", "flavours": {}},
            "native": {
                "base_url": "https://e/",
                "builds": {"macos-arm64": [{"name": "a.tar.gz", "size": 1, "sha256": "abc"}]},
            },  # noqa: E501
        }
        with self.assertRaises(DataFileError):
            Pins.from_dict(data)


class ParseTest(unittest.TestCase):
    def test_vm_stat_uses_its_own_page_size(self):
        values = macos.parse_vm_stat(VM_STAT)
        self.assertEqual(values["free"], 100000 * 16384)
        # vm_stat lists speculative pages apart from free ones; both can be used at once.
        self.assertEqual(macos.available_from_vm_stat(values), 950000 * 16384)

    def test_swap_and_version(self):
        self.assertEqual(macos.parse_swapusage(SWAP), (2048 * 1024**2, 1536 * 1024**2))
        self.assertEqual(macos.parse_version("13.3.1\n"), (13, 3, 1))
        self.assertEqual(macos.parse_version("15\n"), (15,))

    def test_list_devices(self):
        devices = macos.parse_list_devices(DEVICES)
        self.assertEqual(devices, [macos.Device("MTL0", "Apple M2 Max", 49152, 49151)])

    def test_estimated_metal_limit(self):
        self.assertEqual(macos.estimated_limit(36 * GIB), 24 * GIB)
        self.assertEqual(macos.estimated_limit(64 * GIB), 48 * GIB)


class PlatformTest(unittest.TestCase):
    def test_a_mac_has_a_desktop_except_over_ssh(self):
        here = detect_platform(FakeHost(), system="Darwin", machine="arm64")
        self.assertEqual((here.os, here.arch, here.headless), ("darwin", "arm64", False))
        remote = detect_platform(FakeHost(env={"SSH_CONNECTION": "1 2 3 4"}), system="Darwin")
        self.assertTrue(remote.headless)

    def test_memory_comes_from_sysctl_and_vm_stat(self):
        values = read_meminfo(mac_host())
        self.assertEqual(values["MemTotal"], 64 * GIB)
        self.assertEqual(values["MemAvailable"], 950000 * 16384)
        self.assertEqual(values["SwapTotal"] - values["SwapFree"], 512 * 1024**2)


class SystemCheckTest(unittest.TestCase):
    def test_apple_silicon_gets_one_unified_pool_and_the_native_build(self):
        report = check_system(mac_host(), load_pins(), system="Darwin", machine="arm64")
        self.assertEqual(report.native.reference, REFERENCE)
        self.assertIsNone(report.flavour)
        self.assertEqual(report.blocking, ())
        (pool,) = report.pools
        self.assertIs(pool.kind, PoolKind.UNIFIED)
        self.assertEqual(pool.total, 64 * GIB)
        self.assertEqual(pool.limit, 48 * GIB)  # three quarters of 64 GiB until Metal says
        self.assertEqual(pool.usable, 48 * GIB)
        self.assertIn("ESTIMATED", pool.name)
        self.assertEqual([g.vendor for g in report.gpus], ["apple"])
        self.assertEqual(report.warnings, ())  # an Apple Silicon Mac needs no warning
        self.assertFalse(any("Docker" in p.message for p in report.problems))

    def test_metal_limit_is_read_once_the_runtime_is_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = root / REFERENCE / "llama-server"
            server.parent.mkdir(parents=True)
            server.write_text("")
            host = mac_host()
            host.programs["llama-server"] = str(server)
            host.commands[(str(server), "--list-devices")] = ok(DEVICES)
            report = check_system(
                host, load_pins(), system="Darwin", machine="arm64", runtime_root=root
            )
        (pool,) = report.pools
        self.assertEqual(pool.limit, 48 * GIB)
        self.assertIn("Metal limit 48 GiB", pool.name)
        self.assertNotIn("ESTIMATED", pool.name)

    def test_metal_itself_is_asked_before_the_runtime_or_the_estimate(self):
        host = mac_host(32 * GIB, metal=25 * GIB)
        host.commands[("llama-server", "--list-devices")] = ok(DEVICES)  # never reached
        report = check_system(host, load_pins(), system="Darwin", machine="arm64")
        (pool,) = report.pools
        self.assertEqual(pool.limit, 25 * GIB)
        self.assertIn("Metal limit 25 GiB", pool.name)
        self.assertNotIn("ESTIMATED", pool.name)
        self.assertEqual(pool.usable, 24 * GIB)  # 32 GiB less the system's 8 GiB is lower

    def test_without_a_gpu_metal_is_not_asked(self):
        host = mac_host(metal=25 * GIB)
        report = check_system(host, load_pins(), system="Darwin", machine="x86_64")
        self.assertIsNone(report.pools[0].limit)

    def test_reading_metal_is_safe_on_any_machine(self):
        limit = macos.read_working_set()
        if sys.platform != "darwin":
            self.assertIsNone(limit)
            self.assertIsNone(LocalHost().metal_working_set())
        else:  # a CI Mac may have no Metal device
            self.assertTrue(limit is None or limit > 0)

    def test_the_system_share_is_not_taken_from_the_metal_limit_again(self):
        # A busy 32 GiB Mac: the limit (2/3, about 21.3 GiB) already leaves the rest to the
        # system, so the whole limit is usable, not the limit less a quarter of it.
        report = check_system(mac_host(32 * GIB), load_pins(), system="Darwin", machine="arm64")
        (pool,) = report.pools
        self.assertEqual(pool.baseline, 8 * GIB)  # a quarter of 32 GiB, the clamp
        self.assertEqual(pool.usable, macos.estimated_limit(32 * GIB))
        self.assertTrue(pool.limited)

    def test_mac_pools_ask_for_a_margin_and_name_apps_to_close(self):
        for machine in ("arm64", "x86_64"):
            with self.subTest(machine):
                report = check_system(mac_host(), load_pins(), system="Darwin", machine=machine)
                (pool,) = report.pools
                self.assertEqual(pool.margin, macos.RECOMMEND_MARGIN)
                self.assertTrue(pool.apps_hint)

    def test_old_macos_is_blocked_and_intel_runs_on_the_cpu(self):
        report = check_system(
            mac_host(version="12.7"), load_pins(), system="Darwin", machine="x86_64"
        )
        self.assertTrue(any("older than 13.3" in p.message for p in report.blocking))
        self.assertEqual(report.native.key, "macos-x64")
        self.assertEqual(report.gpus, ())
        self.assertIs(report.pools[0].kind, PoolKind.RAM)
        intel = [p for p in report.problems if "Intel Mac" in p.message]
        self.assertEqual([p.severity for p in intel], [Severity.WARNING])


def make_archive(folder: Path, members: dict[str, str], links: dict[str, str] = ()) -> Path:
    """A .tar.gz with every file under one top folder, like the llama.cpp release."""
    source = folder / "source" / "llama-b11552"
    source.mkdir(parents=True)
    for name, text in members.items():
        (source / name).write_text(text)
        os.chmod(source / name, 0o755)
    archive = folder / "llama-b11552-bin-macos-arm64.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="llama-b11552")
        for name, target in dict(links).items():
            info = tarfile.TarInfo(f"llama-b11552/{name}")
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return archive


def pins_for(archive: Path) -> Pins:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    asset = NativeAsset(archive.name, f"https://example.invalid/{archive.name}", 1, digest)
    return Pins(
        build="b11552",
        images={},
        native={"macos-arm64": NativeBuild("macos-arm64", "b11552", (asset,))},
    )  # noqa: E501


class NativeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paths = resolve_paths({}, self.root, "darwin")
        self.host = FakeHost(programs={"caffeinate": "/usr/bin/caffeinate"})
        self.http = FakeHttp()
        self.profile = native_profile(self.root)

    def runtime(self, **kwargs) -> NativeRuntime:
        kwargs.setdefault("pins", load_pins())
        kwargs.setdefault("clock", ticking_clock())
        return NativeRuntime(
            self.host,
            self.paths,
            http=self.http,
            sleep=lambda s: None,
            out=io.StringIO(),
            pick_port=lambda: 49200,
            **kwargs,
        )


class InstallTest(NativeTestCase):
    def fetch_from(self, archive: Path):
        def fetch(asset, dest, on_progress):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read_bytes())
            size = len(archive.read_bytes())
            on_progress(Status(dest.name, "download", size, size))

        return fetch

    def test_pull_shows_download_progress(self):
        archive = make_archive(self.root, {"llama-server": "s"})
        runtime = self.runtime(pins=pins_for(archive), fetch=self.fetch_from(archive))
        runtime.pull(REFERENCE)
        text = runtime.out.getvalue()
        self.assertTrue(text.startswith("  From the official llama.cpp release: https://"))
        self.assertRegex(text, r"\n  \[#+\] 100%")
        self.assertIn("  Matches its pinned sha256.", text)

    def test_pull_checks_and_unpacks_the_whole_folder(self):
        archive = make_archive(
            self.root,
            {"llama-server": "s", "llama-fit-params": "f", "libggml.0.dylib": "l"},
            links={"libggml.dylib": "libggml.0.dylib"},
        )
        runtime = self.runtime(pins=pins_for(archive), fetch=self.fetch_from(archive))
        self.assertFalse(runtime.image_present(REFERENCE))
        runtime.pull(REFERENCE)
        folder = runtime.folder(REFERENCE)
        self.assertTrue(runtime.image_present(REFERENCE))
        self.assertTrue(os.access(folder / "llama-server", os.X_OK))
        self.assertTrue((folder / "libggml.dylib").is_symlink())
        leftovers = [p.name for p in folder.parent.iterdir()]
        self.assertEqual(leftovers, [REFERENCE])  # no archive or staging folder left
        self.assertEqual(runtime.remove_image(REFERENCE), ("deleted", ""))
        self.assertFalse(folder.exists())

    def test_an_archive_that_does_not_match_its_pin_is_not_installed(self):
        archive = make_archive(self.root, {"llama-server": "s"})
        pins = pins_for(archive)
        archive.write_bytes(archive.read_bytes() + b"tampered")
        runtime = self.runtime(pins=pins, fetch=self.fetch_from(archive))
        with self.assertRaisesRegex(ServerError, "does not match its pinned sha256"):
            runtime.pull(REFERENCE)
        self.assertFalse(runtime.folder(REFERENCE).exists())

    def test_members_outside_the_folder_are_refused(self):
        archive = make_archive(self.root, {"llama-server": "s"}, links={"evil": "../../../etc"})
        runtime = self.runtime(pins=pins_for(archive), fetch=self.fetch_from(archive))
        with self.assertRaisesRegex(ServerError, "not safe to unpack"):
            runtime.pull(REFERENCE)
        self.assertEqual(list((self.paths.data / "runtime").iterdir()), [])

    def test_only_tar_archives_are_unpacked(self):
        with self.assertRaises(ServerError):
            unpack(self.root / "x.zip", self.root / "out")

    def test_quarantine_is_detected(self):
        folder = install_fake_runtime(self.paths)
        self.host.programs["xattr"] = "/usr/bin/xattr"
        command = ("xattr", "-p", QUARANTINE, str(folder / "llama-server"))
        self.host.commands[command] = ok("0081;6543;Safari;\n")
        self.assertTrue(self.runtime().quarantined(REFERENCE))


class ProcessTest(NativeTestCase):
    def setUp(self):
        super().setUp()
        self.folder = install_fake_runtime(self.paths)

    def test_start_runs_the_binary_on_127_0_0_1_and_keeps_the_mac_awake(self):
        runtime = self.runtime()
        self.assertEqual(runtime.start(self.profile), "started")
        argv, log, cwd = self.host.started[0]
        self.assertEqual(argv[0], str(self.folder / "llama-server"))
        self.assertEqual(argv[argv.index("--host") + 1], "127.0.0.1")
        self.assertEqual(argv[argv.index("--port") + 1], "18400")
        self.assertEqual(argv[argv.index("-m") + 1], self.profile.model.parts[0])
        self.assertNotIn("--load-mode", argv)
        self.assertEqual(log, self.paths.logs / f"{self.profile.name}.log")
        self.assertEqual(cwd, self.folder)
        pid = read_json(runtime.run_file(self.profile.name))["pid"]
        self.assertIn(("caffeinate", "-i", "-w", str(pid)), self.host.spawned)
        self.assertTrue(runtime.status(self.profile).alive)
        self.assertEqual(runtime.host_port(self.profile), 18400)
        self.assertEqual(runtime.start(self.profile), "reused")

    def test_the_cpu_build_puts_no_layers_on_a_gpu(self):
        config = native_config(native_profile(self.root, flavour="cpu"), 18400)
        self.assertEqual(config.gpu_layers, 0)

    def test_other_settings_are_refused_and_name_the_chat_log(self):
        runtime = self.runtime()
        runtime.start(self.profile)
        with self.assertRaisesRegex(ServerError, "the chat log is on for one"):
            runtime.start(self.profile, proxied=True)
        self.profile.runtime.context = 131072
        with self.assertRaisesRegex(ServerError, "already running with different settings"):
            runtime.start(self.profile)

    def test_behind_the_chat_log_the_server_gets_its_own_port(self):
        runtime = self.runtime()
        runtime.start(self.profile, proxied=True)
        argv = self.host.started[0][0]
        self.assertEqual(argv[argv.index("--port") + 1], "49200")
        self.assertEqual(runtime.host_port(self.profile), 49200)
        self.assertEqual(runtime.api_url(self.profile), "http://127.0.0.1:49200")
        self.assertEqual(runtime.start(self.profile, proxied=True), "reused")

    def test_stop_asks_first_then_forces(self):
        runtime = self.runtime(clock=ticking_clock(5.0))
        runtime.start(self.profile)
        pid = read_json(runtime.run_file(self.profile.name))["pid"]
        self.host.ignore_terminate.add(pid)
        self.assertTrue(runtime.stop(self.profile))
        self.assertEqual((self.host.terminated, self.host.killed), ([pid], [pid]))
        self.assertFalse(runtime.run_file(self.profile.name).exists())
        self.assertEqual(runtime.status(self.profile).state, "missing")
        self.assertFalse(runtime.stop(self.profile))

    def test_a_stale_run_file_never_touches_another_program(self):
        runtime = self.runtime()
        path = runtime.run_file(self.profile.name)
        path.parent.mkdir(parents=True)
        path.write_text('{"pid": 4321, "port": 18400, "binary": "/x/llama-server"}')
        self.host.processes[4321] = "/usr/bin/vim notes.txt"
        self.assertFalse(runtime.status(self.profile).alive)
        self.assertFalse(runtime.stop(self.profile))
        self.assertEqual(self.host.terminated, [])
        self.assertEqual(runtime.start(self.profile), "started")

    def test_running_profiles_and_remove_by_name(self):
        runtime = self.runtime()
        runtime.start(self.profile)
        (running,) = runtime.running_profiles()
        self.assertEqual(running.name, container_name(self.profile.name))
        self.assertTrue(runtime.remove(running.name))
        self.assertEqual(runtime.running_profiles(), [])

    def test_a_server_that_dies_while_loading_shows_its_log(self):
        self.http.health = [503]
        self.host.start_log = "llama_model_load: error loading model: out of memory\n"
        runtime = self.runtime()
        runtime.start(self.profile)
        self.host.processes.clear()  # it exited
        with self.assertRaisesRegex(ServerError, "out of memory"):
            runtime.wait_ready(self.profile)

    def test_fit_check_uses_the_native_tool(self):
        runtime = self.runtime()
        tool = self.folder / "llama-fit-params"
        self.host.programs["llama-fit-params"] = str(tool)
        args = fit_params_args(native_config(self.profile, 18400))[1:]
        self.host.commands[(str(tool), *args)] = ok("MTL0 34667 5182 757\nHost 515 0 272\n")
        self.assertEqual(runtime.fit_check(self.profile).devices["MTL0"], [34667, 5182, 757])

    def test_other_model_servers_are_named(self):
        self.host.programs["ps"] = "/bin/ps"
        ours = self.folder / "llama-server"
        self.host.commands[("ps", "-axo", "pid=,command=")] = ok(
            "  101 /Applications/Ollama.app/Contents/Resources/ollama serve\n"
            "  202 /usr/bin/python3 -m http.server\n"
            f"  303 {ours} -m model.gguf\n"
        )
        self.assertEqual(self.runtime().other_model_servers(), ["ollama (pid 101)"])


class LogTest(NativeTestCase):
    def test_the_follower_shows_new_lines_until_the_server_stops(self):
        log = self.root / "server.log"
        log.write_text("old line\n")
        follower = FileFollower(
            log, alive=lambda: False, sleep=lambda s: None, clock=ticking_clock()
        )
        with open(log, "a") as handle:
            handle.write("new line\npartial")
        self.assertEqual(list(follower.lines()), ["new line", "partial"])

    def test_logs_and_follow_logs_read_the_file(self):
        install_fake_runtime(self.paths)
        self.host.start_log = "one\ntwo\nthree\n"
        runtime = self.runtime()
        runtime.start(self.profile)
        self.assertEqual(runtime.logs(container_name(self.profile.name), 2), "two\nthree")
        self.host.processes.clear()
        self.assertEqual(runtime.follow_logs(self.profile, 1), 0)
        self.assertEqual(runtime.out.getvalue(), "three\n")


class MacCommandsTest(unittest.TestCase):
    """The everyday commands on a simulated Mac."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = FakeHost(env={"HOME": str(self.root)})
        self.out = io.StringIO()
        self.app = App(
            host=self.host,
            paths=resolve_paths({}, self.root, "darwin"),
            out=self.out,
            err=io.StringIO(),
            os_name="darwin",
            machine="arm64",
            read_line=lambda prompt: "",
        )
        self.profile = native_profile(self.root)
        save_profile(self.app.paths, self.profile)
        self.folder = install_fake_runtime(self.app.paths)
        self.http = FakeHttp()
        patcher = mock.patch.object(
            launch,
            "make_runtime",
            lambda app: NativeRuntime(
                app.host, app.paths, http=self.http, sleep=lambda s: None, out=app.out
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *args):
        return cli.main(list(args), app=self.app)

    def test_make_runtime_picks_the_backend_by_os(self):
        mock.patch.stopall()
        self.assertIsInstance(launch.make_runtime(self.app), NativeRuntime)
        linux = dataclasses.replace(self.app, os_name="linux")
        self.assertIsInstance(launch.make_runtime(linux), DockerRuntime)

    def test_launch_status_and_stop_without_docker(self):
        self.assertEqual(self.run_cli("launch", "--here", "--no-monitor"), 0)
        self.assertIn("is ready at http://127.0.0.1:18400", self.out.getvalue())
        self.assertFalse(any(call[0] == "docker" for call in self.host.calls))
        self.run_cli("status", "--command")
        status = self.out.getvalue()
        self.assertIn("Process", status)
        self.assertIn(f"{self.folder / 'llama-server'} -m ", status)
        self.run_cli("stop")
        self.assertIn(f"Stopped {self.profile.name}; memory released.", self.out.getvalue())
        self.assertEqual(self.host.processes, {})

    def test_a_missing_runtime_is_offered_with_its_size(self):
        import shutil

        shutil.rmtree(self.folder)
        with mock.patch.object(NativeRuntime, "pull") as pull:
            asked = []
            self.app.read_line = lambda prompt: asked.append(prompt) or "n"
            self.assertEqual(self.run_cli("launch", "--here", "--no-monitor"), 1)
        pull.assert_not_called()
        text = self.out.getvalue() + "".join(asked)
        self.assertIn("The model server (llama.cpp b11552 for macOS on Apple Silicon)", text)
        self.assertIn("Download it now (about 12 MB)?", text)

    def fake_osacompile(self):
        """osacompile that makes the app folder, as the real one does; argv kept for checks."""
        self.compiled = []
        run = self.host.run

        def fake(argv, timeout=10.0):
            if argv[0] != "osacompile":
                return run(argv, timeout)
            self.compiled.append(list(argv))
            (Path(argv[2]) / "Contents" / "MacOS").mkdir(parents=True)
            return ok("")

        self.host.run = fake

    def test_setup_shortcuts_build_an_app(self):
        from ornith_launcher.uninstall import remove_created

        (self.root / "Desktop").mkdir()
        self.host.env["PATH"] = "/opt/homebrew/bin:/usr/bin:/bin"
        self.profile.terminal = {"id": "iterm2"}
        save_profile(self.app.paths, self.profile)
        self.fake_osacompile()
        answers = iter(["", "y"])  # desktop icon (default yes), Applications folder yes
        self.app.read_line = lambda prompt: next(answers)
        self.assertEqual(self.run_cli("setup", "--plain", "--shortcuts", self.profile.name), 0)
        saved = load_profile(self.app.paths, self.profile.name)
        icon = Path(saved.integrations["desktop_copy"])
        menu = Path(saved.integrations["shortcut"])
        self.assertEqual(icon, self.root / "Desktop" / f"Ornith {self.profile.name}.app")
        self.assertEqual(menu, self.root / "Applications" / f"Ornith {self.profile.name}.app")
        self.assertTrue(icon.is_dir())
        argv = self.compiled[0]
        self.assertEqual(argv[:3], ["osacompile", "-o", str(icon)])
        script = "\n".join(argv[4::2])
        # The PATH setup ran with, so the app finds the same Python; and always a window.
        self.assertIn("PATH=/opt/homebrew/bin:/usr/bin:/bin; export PATH; exec ", script)
        self.assertIn(f"launch {self.profile.name} --window", script)
        self.assertIn("display alert", script)
        text = self.out.getvalue()
        self.assertIn("macOS asks whether it may control iTerm2; choose Allow.", text)
        self.assertIn("Find it there in Finder, Launchpad or Spotlight.", text)
        self.assertEqual(load_installed(self.app.paths).shortcuts, [str(icon), str(menu)])
        # Asking again finds both apps, folders though they are, and adds nothing.
        self.assertEqual(self.run_cli("setup", "--plain", "--shortcuts", self.profile.name), 0)
        self.assertEqual(len(self.compiled), 2)
        remove_created(self.app, saved)
        self.assertFalse(icon.exists() or menu.exists())

    def app_macos_will_not_let_us_delete(self):
        """A launch app that has run: App Management refuses to delete what is inside."""
        import shutil

        from ornith_launcher import uninstall

        (self.root / "Desktop").mkdir()
        self.fake_osacompile()
        answers = iter(["", "n"])
        self.app.read_line = lambda prompt: next(answers)
        self.run_cli("setup", "--plain", "--shortcuts", self.profile.name)
        saved = load_profile(self.app.paths, self.profile.name)
        real_rmtree = shutil.rmtree  # what Finder does, for the test that lets it
        refused = PermissionError(1, "Operation not permitted")
        patcher = mock.patch.object(uninstall.shutil, "rmtree", side_effect=refused)
        patcher.start()
        self.addCleanup(patcher.stop)
        return saved, Path(saved.integrations["desktop_copy"]), real_rmtree

    def test_an_app_macos_protects_goes_to_the_trash_through_finder(self):
        from ornith_launcher.uninstall import remove_created

        saved, icon, rmtree = self.app_macos_will_not_let_us_delete()
        run = self.host.run
        finder = []

        def fake(argv, timeout=10.0):
            if argv[0] == "osascript":
                finder.append(argv[2])
                rmtree(icon)  # Finder moves it to the Trash
                return ok("")
            return run(argv, timeout)

        self.host.run = fake
        remove_created(self.app, saved)
        self.assertFalse(icon.exists())
        self.assertIn('tell application "Finder" to delete (POSIX file "', finder[0])
        self.assertIn("to the Trash (macOS let only Finder remove it)", self.out.getvalue())
        self.assertNotIn(str(icon), load_installed(self.app.paths).shortcuts)

    def test_an_app_nothing_may_delete_is_named_and_the_rest_goes_on(self):
        from ornith_launcher.uninstall import remove_created

        saved, icon, _ = self.app_macos_will_not_let_us_delete()
        saved.runtime.template.source, saved.runtime.template.file = (
            "patched",
            str(self.root / "patched.jinja"),
        )
        Path(saved.runtime.template.file).write_text("t")
        remove_created(self.app, saved)  # no osascript here, so Finder cannot help either
        text = self.out.getvalue()
        self.assertIn(f"Could not delete {icon} (Operation not permitted).", text)
        self.assertIn("Privacy & Security > App Management", text)
        self.assertTrue(icon.exists())
        self.assertIn(str(icon), load_installed(self.app.paths).shortcuts)  # tried again later
        self.assertFalse(Path(saved.runtime.template.file).exists())  # the rest went on

    def test_setup_shortcuts_fall_back_to_command_files_without_osacompile(self):
        (self.root / "Desktop").mkdir()
        answers = iter(["", "y"])
        self.app.read_line = lambda prompt: next(answers)
        self.assertEqual(self.run_cli("setup", "--plain", "--shortcuts", self.profile.name), 0)
        saved = load_profile(self.app.paths, self.profile.name)
        icon = Path(saved.integrations["desktop_copy"])
        self.assertEqual(icon, self.root / "Desktop" / f"Ornith {self.profile.name}.command")
        self.assertIn(f"launch {self.profile.name}", icon.read_text())
        self.assertTrue(os.access(icon, os.X_OK))
        menu = Path(saved.integrations["shortcut"])
        self.assertEqual(menu.parent, self.root / "Applications")
        self.assertEqual(menu.suffix, ".command")
        text = self.out.getvalue()
        self.assertIn("osacompile could not build an app, so this is a .command file", text)
        self.assertIn("If macOS asks before opening it the first time, choose Open.", text)
        self.assertNotIn(("gio",), [call[:1] for call in self.host.calls])
        self.assertEqual(load_installed(self.app.paths).shortcuts, [str(icon), str(menu)])

    def test_each_finishing_question_starts_a_fresh_screen(self):
        from types import SimpleNamespace

        from ornith_launcher.integrations import setup as integrations
        from ornith_launcher.setup_profile import ask_shortcuts, offer_chat_log
        from ornith_launcher.ui.prompts import PlainPrompter

        titles = []
        prompter = PlainPrompter(read_line=lambda prompt: "n", out=self.out)
        prompter.new_screen = titles.append
        plan = SimpleNamespace(report=SimpleNamespace(platform=SimpleNamespace(headless=False)))
        integrations.choose(self.app, PlainPrompter(read_line=lambda p: "", out=self.out))
        offer_chat_log(self.app, prompter, plan, self.profile)
        (self.root / "Desktop").mkdir()
        ask_shortcuts(self.app, prompter, self.profile)
        self.assertEqual(titles, ["Chat log", "Launch icon"])
        self.assertIn("\nCoding tools\n\n", self.out.getvalue())  # its own screen too

    def test_launch_window_always_opens_one(self):
        # "This terminal" has no terminal to mean from an app: the system's one opens.
        self.profile.terminal = {"id": "this"}
        save_profile(self.app.paths, self.profile)
        self.host.programs["open"] = "/usr/bin/open"
        self.assertEqual(self.run_cli("launch", self.profile.name, "--window"), 0)
        ((argv),) = self.host.spawned
        self.addCleanup(Path(argv[3]).unlink, missing_ok=True)
        self.assertEqual(argv[:3], ("open", "-a", "Terminal"))
        self.assertIn("--here", Path(argv[3]).read_text())
        self.assertEqual(self.host.processes, {})  # nothing ran in the app itself

    def test_launch_window_fails_rather_than_run_unseen(self):
        self.profile.terminal = {"id": "this"}
        save_profile(self.app.paths, self.profile)
        self.assertEqual(self.run_cli("launch", self.profile.name, "--window"), 1)  # no "open"
        err = self.app.err.getvalue()
        self.assertIn("no terminal window could be opened", err)
        self.assertIn(f"ornith-launcher launch {self.profile.name}", err)
        self.assertEqual(self.host.started, [])

    def test_uninstall_everything_deletes_the_runtime_it_downloaded(self):
        from ornith_launcher.uninstall import apply_removal, plan_removal

        record(self.app.paths, "runtimes", REFERENCE)
        runtime = launch.make_runtime(self.app)
        removal = plan_removal(self.app, [self.profile], everything=True)
        self.assertEqual(removal.images, [REFERENCE])
        apply_removal(self.app, removal, runtime)
        self.assertFalse(self.folder.exists())
        self.assertIn("Deleted the llama.cpp runtime.", self.out.getvalue())


class SetupOnMacTest(unittest.TestCase):
    def test_the_profile_records_the_native_build(self):
        from ornith_launcher import setup_profile
        from tests.test_wizard import WizardTest

        case = WizardTest("test_fresh_machine_browses_and_downloads")
        case.setUp()
        self.addCleanup(case.doCleanups)
        wizard = case.wizard(["", "d", "", "", "", "", ""])
        selection = wizard.run()
        report = dataclasses.replace(
            wizard.plan.report, flavour=None, native=load_pins().native["macos-arm64"]
        )
        plan = dataclasses.replace(wizard.plan, report=report)
        built = setup_profile.build_profile(case.app, selection, plan, "mac-test", wizard.settings)
        runtime = built.runtime
        self.assertEqual(
            (runtime.image, runtime.flavour, runtime.device_args), (REFERENCE, "metal", [])
        )
        self.assertFalse(runtime.direct_io)


class MacTerminalTest(unittest.TestCase):
    def test_terminal_is_the_default_and_iterm_is_found(self):
        host = FakeHost(env={"HOME": "/Users/a"}, files={"/Applications/iTerm.app/Info": ""})
        found = detect_terminals(host, "darwin")
        self.assertEqual(found.default.id, "terminal-app")
        self.assertEqual([t.id for t in found.installed], ["terminal-app", "iterm2"])

    def test_the_current_terminal_comes_from_term_program(self):
        self.assertEqual(current_terminal({"TERM_PROGRAM": "Apple_Terminal"}), "terminal-app")
        self.assertTrue(runs_in("iterm2", {"TERM_PROGRAM": "iTerm.app"}))

    def test_a_window_opens_from_a_command_script(self):
        host = FakeHost(programs={"open": "/usr/bin/open"})
        terminal = detect_terminals(host, "darwin").default
        self.assertTrue(
            open_in_terminal(host, terminal, ["/x/ornith-launcher", "launch", "a"], "Ornith: a")
        )
        argv = host.spawned[0]
        self.assertEqual(argv[:3], ("open", "-a", "Terminal"))
        script = Path(argv[3])
        self.addCleanup(script.unlink, missing_ok=True)
        self.assertEqual(
            script.read_text(), command_script(["/x/ornith-launcher", "launch", "a"], "Ornith: a")
        )
        self.assertTrue(os.access(script, os.X_OK))

    def iterm(self, reply):
        host = FakeHost(env={"HOME": "/Users/a"}, files={"/Applications/iTerm.app/Info": ""})
        self.ran = []
        host.run = lambda argv, timeout=10.0: self.ran.append((list(argv), timeout)) or reply
        return host, detect_terminals(host, "darwin").installed[1]

    def test_iterm_gets_the_script_typed_in_so_it_does_not_ask_to_run_a_file(self):
        host, iterm = self.iterm(ok(""))
        command = ["/x/ornith-launcher", "launch", "a"]
        self.assertTrue(open_in_terminal(host, iterm, command, "Ornith: a"))
        self.assertEqual(host.spawned, [])  # never "open -a iTerm", which asks "OK to run"
        ((argv, timeout),) = self.ran
        self.assertEqual(argv[:2], ["osascript", "-e"])
        self.assertEqual(timeout, APPLESCRIPT_TIMEOUT)  # room for macOS's one-time question
        self.assertIn("create window with default profile", argv[2])
        script = Path(re.search(r'write text "(.+); exit"', argv[2]).group(1))
        self.addCleanup(script.unlink, missing_ok=True)
        self.assertEqual(script.read_text(), command_script(command, "Ornith: a"))

    def test_a_refused_iterm_window_leaves_no_script_and_names_the_fix(self):
        host, iterm = self.iterm(CommandResult(1, "", "Not authorized to send Apple events"))
        self.assertFalse(open_in_terminal(host, iterm, ["/x/ornith-launcher"], "Ornith: a"))
        script = Path(re.search(r'write text "(.+); exit"', self.ran[0][0][2]).group(1))
        self.assertFalse(script.exists())
        self.assertIn("Privacy & Security > Automation", iterm.failure_hint)

    def test_the_iterm_script_quotes_odd_paths(self):
        text = iterm_script(Path('/tmp/a "b" c.command'))
        self.assertIn('write text "\'/tmp/a \\"b\\" c.command\'; exit"', text)

    def test_the_script_removes_itself_and_quotes_paths(self):
        text = command_script(["/Users/a b/ornith-launcher", "launch", "a"], "Ornith: a")
        self.assertIn('rm -f "$0"', text)
        self.assertIn("exec '/Users/a b/ornith-launcher' launch a", text)


class BenchOnMacTest(unittest.TestCase):
    def test_perplexity_runs_the_native_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            prof = native_profile(Path(tmp))
            args = native_perplexity_args(Path("/rt/llama-perplexity"), prof, Path("/t.txt"), 2)
        self.assertEqual(
            args[:5], ["/rt/llama-perplexity", "-m", prof.model.parts[0], "-f", "/t.txt"]
        )
        self.assertIn("--rope-scaling", args)
        self.assertNotIn("docker", args)


if __name__ == "__main__":
    unittest.main()
