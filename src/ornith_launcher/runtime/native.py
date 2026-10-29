"""Running llama.cpp without Docker: the pinned release build as a child process (macOS).

The archive for this machine (``pins.json`` -> ``native``) is downloaded once,
checked against its sha256 and unpacked whole into ``<data>/runtime/<reference>/``,
because the binaries find their libraries next to themselves (``@rpath`` is the
binary's own folder). Every member is checked before unpacking: no absolute
paths, no ``..`` and no link that points outside the folder.

One server process per profile, started in its own session so Ctrl+C in the
monitor reaches only the monitor and closing the window leaves the server
running, as with a container. A run file ``<state>/run/server-<profile>.json``
records its pid, port, binary and a hash of its arguments, so a later launch can
tell "already running as configured" (reuse it) from "running with other
settings" (refuse), and a pid is only acted on while that process is still our
binary. The server listens on 127.0.0.1 only, because its API has no
authentication.
"""

from __future__ import annotations

import hashlib
import posixpath
import shlex
import shutil
import socket
import sys
import tarfile
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, TextIO, Union

from ornith_launcher.config import Paths, atomic_write_json, read_json
from ornith_launcher.download import ProgressLine, Status
from ornith_launcher.pins import NativeAsset, NativeBuild, Pins, load_pins
from ornith_launcher.profiles import Profile
from ornith_launcher.runtime.api import ServerApi, ServerError
from ornith_launcher.runtime.docker import (
    ARGS_LABEL,
    BOOT_TIMEOUT,
    POLL,
    PREFIX,
    PROFILE_LABEL,
    Container,
    DockerRuntime,
    FitCheck,
    _progress_line,
    container_name,
    parse_fit_params,
)
from ornith_launcher.runtime.http import Http, UrllibHttp
from ornith_launcher.runtime.server import ServerConfig, fit_params_args, server_args
from ornith_launcher.system.host import Host

STOP_TIMEOUT = 20.0  # seconds after SIGTERM before SIGKILL
LOG_POLL = 0.25
ALIVE_EVERY = 2.0  # seconds between process checks while following a log
SERVER = "llama-server"
FIT_PARAMS = "llama-fit-params"
PERPLEXITY = "llama-perplexity"
QUARANTINE = "com.apple.quarantine"
# Other model servers that would compete for the same memory, by executable name.
_OTHER_SERVERS = ("ollama", "llama-server", "vllm", "sglang", "mlx_lm", "lmdeploy")
_PLATFORMS = {"macos-arm64": "macOS on Apple Silicon", "macos-x64": "macOS on Intel"}


def runtime_root(paths: Paths) -> Path:
    return paths.data / "runtime"


def native_config(profile: Profile, port: int) -> ServerConfig:
    """The profile's server settings with real file paths, on 127.0.0.1."""
    runtime = profile.runtime
    template = runtime.template
    return ServerConfig(
        model=profile.model.parts[0],
        alias=runtime.alias,
        context=runtime.context,
        slots=runtime.slots,
        kv_type=runtime.kv_type,
        native_context=runtime.native_context,
        yarn_factor=runtime.yarn_factor,
        mmproj=profile.model.mmproj,
        template_file=template.file if template.source == "patched" else None,
        cache_ram_mib=runtime.cache_ram_mib,
        sampling=runtime.sampling,
        host="127.0.0.1",
        port=port,
        gpu_layers=0 if runtime.flavour == "cpu" else 999,
        direct_io=runtime.direct_io,
        cpu_moe=runtime.placement == "moe-offload",
        extra_args=tuple(runtime.extra_args),
    )


def args_hash(argv: Sequence[str]) -> str:
    return hashlib.sha256("\0".join(argv).encode()).hexdigest()[:16]


def free_port() -> int:
    """A port nobody listens on, for the server behind the chat log proxy."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# Unpacking --------------------------------------------------------------------------------


def unsafe_reason(member: tarfile.TarInfo) -> str | None:
    """Why an archive member must not be unpacked, or None when it stays inside the folder."""
    name = PurePosixPath(member.name)
    if name.is_absolute() or ".." in name.parts:
        return f"{member.name} points outside the folder"
    if member.isdev() or member.isfifo():
        return f"{member.name} is a device file"
    if member.issym() or member.islnk():
        link = member.linkname
        if posixpath.isabs(link):
            return f"{member.name} links to an absolute path"
        base = posixpath.dirname(member.name) if member.issym() else ""
        target = posixpath.normpath(posixpath.join(base, link))
        if target == ".." or target.startswith("../"):
            return f"{member.name} links outside the folder"
    return None


def unpack(archive: Path, dest: Path) -> None:
    """Unpack a release ``.tar.gz`` into ``dest`` after checking every member."""
    if not archive.name.endswith((".tar.gz", ".tgz")):
        raise ServerError(f"{archive.name} is not a .tar.gz archive; it cannot be unpacked here")
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            reason = unsafe_reason(member)
            if reason is not None:
                raise ServerError(
                    f"the llama.cpp archive is not safe to unpack: {reason}",
                    hint="Report this at https://github.com/ShourieG/ornith-launcher/issues",
                )
        if hasattr(tarfile, "data_filter"):  # Python 3.12, and 3.9.17+ security releases
            tar.extractall(dest, members=members, filter="data")
        else:
            tar.extractall(dest, members=members)


def _single_folder(path: Path) -> Path:
    """The archive's own top folder (``llama-b11552/``), or the folder itself."""
    entries = list(path.iterdir())
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else path


def _download(asset: NativeAsset, dest: Path, on_progress: Callable[[Status], None]) -> None:
    from ornith_launcher.download import Downloader, HttpRangeSource, Target

    Downloader(source=HttpRangeSource(), segments=1, on_progress=on_progress).fetch(
        Target(asset.url, dest, asset.size, asset.sha256)
    )


# Following a log file ----------------------------------------------------------------------


class FileFollower:
    """New lines of a log file, until closed or the server stops (``tail -f`` in Python)."""

    def __init__(
        self,
        path: Path,
        alive: Callable[[], bool],
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self.alive = alive
        self.sleep = sleep
        self.clock = clock
        self._closed = False
        try:
            self._offset = path.stat().st_size  # only lines written from now on
        except OSError:
            self._offset = 0

    def lines(self) -> Iterator[str]:
        checked = self.clock()
        pending = ""
        while not self._closed:
            chunk = self._read()
            if chunk:
                pending += chunk
                *complete, pending = pending.split("\n")
                for line in complete:
                    yield line.rstrip("\r")
                continue
            if self.clock() - checked >= ALIVE_EVERY:
                checked = self.clock()
                if not self.alive():
                    break
            self.sleep(LOG_POLL)
        if pending:
            yield pending.rstrip("\r")

    def _read(self) -> str:
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self._offset)
                data = handle.read()
        except OSError:
            return ""
        self._offset += len(data)
        return data.decode("utf-8", "replace")

    def close(self) -> None:
        self._closed = True


def last_lines(path: Path, count: int) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-count:] if count else []


# The backend ------------------------------------------------------------------------------


@dataclass
class NativeRuntime(ServerApi):
    host: Host
    paths: Paths
    pins: Pins = field(default_factory=load_pins)
    http: Http = field(default_factory=UrllibHttp)
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    out: TextIO = field(default_factory=lambda: sys.stdout)
    # Downloads and checks one archive, reporting its progress.
    fetch: Callable[[NativeAsset, Path, Callable[[Status], None]], None] = _download
    pick_port: Callable[[], int] = free_port

    # How the rest of the launcher names what this backend runs.
    kind = "native"
    record_kind = "runtimes"  # the key in installed.json
    state_column = "Process"
    server_noun = "llama.cpp runtime"
    fetched = "downloaded"

    # Names and files ----------------------------------------------------------------------

    def build(self, reference: str) -> NativeBuild | None:
        return next((b for b in self.pins.native.values() if b.reference == reference), None)

    def describe(self, reference: str) -> str:
        build = self.build(reference)
        if build is None:
            return f"llama.cpp runtime {reference}"
        return f"llama.cpp {build.build} for {_PLATFORMS.get(build.key, build.key)}"

    def download_size(self, reference: str) -> str:
        build = self.build(reference)
        return f"about {build.size / 1000**2:.0f} MB" if build else "a small download"

    def same_reference(self, one: str, other: str) -> bool:
        return one == other

    def folder(self, reference: str) -> Path:
        return runtime_root(self.paths) / reference

    def tool(self, reference: str, name: str) -> Path:
        return self.folder(reference) / name

    def run_file(self, profile_name: str) -> Path:
        return self.paths.state / "run" / f"server-{profile_name}.json"

    def log_file(self, profile_name: str) -> Path:
        return self.paths.logs / f"{profile_name}.log"

    def log_command(self, profile: Profile) -> str:
        return f"tail -n 100 {shlex.quote(str(self.log_file(profile.name)))}"

    def server_argv(self, profile: Profile, port: int) -> list[str]:
        binary = self.tool(profile.runtime.image, SERVER)
        return [str(binary), *server_args(native_config(profile, port))]

    def command(self, profile: Profile) -> list[str]:
        """The full command that starts this profile's server (``status --command``)."""
        return self.server_argv(profile, profile.runtime.port)

    def expected_hash(self, profile: Profile, proxied: bool) -> str:
        # Behind the proxy the port is picked at start, so the hash leaves it out.
        port = 0 if proxied else profile.runtime.port
        return args_hash(self.server_argv(profile, port))

    # Installing the runtime ("images" for Docker) -----------------------------------------

    def image_present(self, reference: str) -> bool:
        return self.tool(reference, SERVER).is_file()

    def pull(self, reference: str) -> None:
        """Download, check and unpack the pinned archive; nothing is left half-installed."""
        build = self.build(reference)
        if build is None:
            raise ServerError(
                f"no pinned llama.cpp build is called {reference}",
                hint="Run ornith-launcher setup again to pick the build for this Mac.",
            )
        root = runtime_root(self.paths)
        downloads = root / ".downloads"
        staging = root / f".{reference}.unpacking"
        target = self.folder(reference)
        try:
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            for asset in build.assets:
                archive = downloads / asset.name
                # Say where it comes from: the llama.cpp project's own release, as pinned.
                print(f"  From the official llama.cpp release: {asset.url}", file=self.out)
                progress = ProgressLine(self.out, indent="  ")
                try:
                    self.fetch(asset, archive, progress.update)
                finally:
                    progress.finish()
                from ornith_launcher.download import sha256_file

                if sha256_file(archive) != asset.sha256:
                    raise ServerError(
                        f"{asset.name} does not match its pinned sha256",
                        hint="Run the same command again to download it afresh.",
                    )
                print("  Matches its pinned sha256.", file=self.out, flush=True)
                unpack(archive, staging)
            folder = _single_folder(staging)
            if not (folder / SERVER).is_file():
                raise ServerError(f"the llama.cpp archive for {build.key} has no {SERVER}")
            shutil.rmtree(target, ignore_errors=True)
            folder.replace(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(downloads, ignore_errors=True)

    def remove_image(self, reference: str) -> tuple[str, str]:
        """Delete an unpacked runtime: ("deleted", "") or ("failed", reason)."""
        folder = self.folder(reference)
        if not folder.exists():
            return "deleted", ""
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            return "failed", exc.strerror or str(exc)
        return "deleted", ""

    def quarantined(self, reference: str) -> bool:
        """Whether macOS marked the runtime as downloaded by a browser (Gatekeeper may block it)."""
        binary = self.tool(reference, SERVER)
        if not binary.is_file() or not self.host.which("xattr"):
            return False
        result = self.host.run(["xattr", "-p", QUARANTINE, str(binary)])
        return result is not None and result.ok

    # Processes ----------------------------------------------------------------------------

    def _info(self, profile_name: str) -> dict[str, Any] | None:
        try:
            info = read_json(self.run_file(profile_name))
            int(info["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return info

    def _alive(self, info: dict[str, Any]) -> bool:
        # A stale file may name a pid that now belongs to another program: check it is ours.
        command = self.host.process_command(int(info["pid"])) or ""
        return bool(info.get("binary")) and str(info["binary"]) in command

    def inspect(self, name: str) -> Container:
        profile_name = name[len(PREFIX) :] if name.startswith(PREFIX) else name
        info = self._info(profile_name)
        if info is None or not self._alive(info):
            return Container(name, "missing")
        labels = {PROFILE_LABEL: profile_name, ARGS_LABEL: str(info.get("args_sha256", ""))}
        return Container(name, "running", str(info.get("reference", "")), labels)

    def status(self, profile: Profile) -> Container:
        return self.inspect(container_name(profile.name))

    def running_profiles(self) -> list[Container]:
        """Our servers that are running, whatever their profile."""
        found = []
        for path in sorted((self.paths.state / "run").glob("server-*.json")):
            container = self.inspect(container_name(path.stem[len("server-") :]))
            if container.alive:
                found.append(container)
        return found

    def other_model_servers(self) -> list[str]:
        """Running processes of other model servers (Ollama, llama-server, ...), as "name (pid)"."""
        result = self.host.run(["ps", "-axo", "pid=,command="], timeout=30)
        if result is None or not result.ok:
            return []
        ours = str(runtime_root(self.paths))
        found = []
        for line in result.stdout.splitlines():
            pid, _, command = line.strip().partition(" ")
            if not command or ours in command:
                continue
            program = command.split()[0].rsplit("/", 1)[-1]
            if any(name in program.lower() for name in _OTHER_SERVERS):
                found.append(f"{program} (pid {pid})")
        return found

    def start(self, profile: Profile, proxied: bool = False) -> str:
        """Start the profile's server; "reused" if it already runs with the same settings."""
        expected = self.expected_hash(profile, proxied)
        info = self._info(profile.name)
        if info is not None and self._alive(info):
            if info.get("args_sha256") == expected:
                return "reused"
            other = self.expected_hash(profile, not proxied)
            raise ServerError(
                f"{profile.name} is already running with different settings"
                + (
                    " (the chat log is on for one and off for the other)"
                    if info.get("args_sha256") == other
                    else ""
                ),
                hint=f"Stop it first: ornith-launcher stop {profile.name}",
            )
        reference = profile.runtime.image
        binary = self.tool(reference, SERVER)
        if not binary.is_file():
            raise ServerError(
                f"the {self.describe(reference)} is not installed",
                hint=f"Install it with: ornith-launcher setup --repair {profile.name}",
            )
        self.run_file(profile.name).unlink(missing_ok=True)  # a stale file from an earlier run
        port = self.pick_port() if proxied else profile.runtime.port
        argv = self.server_argv(profile, port)
        pid = self.host.start_process(argv, self.log_file(profile.name), cwd=binary.parent)
        if pid is None:
            raise ServerError(
                f"the server did not start: {binary} could not be run",
                hint=f"See the full command with: ornith-launcher status {profile.name} --command",
            )
        atomic_write_json(
            self.run_file(profile.name),
            {
                "pid": pid,
                "port": port,
                "binary": str(binary),
                "reference": reference,
                "args_sha256": expected,
                "started": time.time(),
            },
        )
        if self.host.which("caffeinate"):
            # Idle sleep would stall a long prefill; this ends by itself with the server.
            self.host.spawn(["caffeinate", "-i", "-w", str(pid)])
        return "started"

    def stop(self, profile: Profile) -> bool:
        return self._stop(profile.name)

    def remove(self, name: str) -> bool:
        """Stop a server by its container-style name; its memory is released at once."""
        return self._stop(name[len(PREFIX) :] if name.startswith(PREFIX) else name)

    def _stop(self, profile_name: str) -> bool:
        info = self._info(profile_name)
        if info is None:
            return False
        stopped = False
        if self._alive(info):
            pid = int(info["pid"])
            self.host.terminate(pid)
            deadline = self.clock() + STOP_TIMEOUT
            while self._alive(info) and self.clock() < deadline:
                self.sleep(LOG_POLL)
            if self._alive(info):
                self.host.kill(pid)
            stopped = True
        self.run_file(profile_name).unlink(missing_ok=True)
        return stopped

    def host_port(self, profile: Profile, cached: bool = True) -> int | None:
        """The port on 127.0.0.1 where the server itself listens, or None if not running."""
        info = self._info(profile.name)
        if info is None or not self._alive(info):
            return None
        return int(info["port"])

    # Logs ---------------------------------------------------------------------------------

    def logs(self, name: str, tail: int = 30) -> str:
        profile_name = name[len(PREFIX) :] if name.startswith(PREFIX) else name
        return "\n".join(last_lines(self.log_file(profile_name), tail)).strip()

    def follow_logs(self, profile: Profile, tail: int) -> int:
        """Show the last ``tail`` log lines, then new ones until the server stops."""
        path = self.log_file(profile.name)
        follower = FileFollower(path, lambda: self.status(profile).alive, self.sleep, self.clock)
        for line in last_lines(path, tail):
            print(line, file=self.out)
        for line in follower.lines():
            print(line, file=self.out, flush=True)
        return 0

    def log_stream(self, profile: Profile) -> FileFollower | None:
        """New log lines from now on, for the server window."""
        return FileFollower(
            self.log_file(profile.name), lambda: self.status(profile).alive, self.sleep, self.clock
        )

    # Server -------------------------------------------------------------------------------

    def wait_ready(
        self,
        profile: Profile,
        on_line: Callable[[str], None] | None = None,
        timeout: float = BOOT_TIMEOUT,
    ) -> float:
        """Wait until /health answers 200; returns the seconds it took."""
        started = self.clock()
        last = ""
        while True:
            if self.http.get(f"{self.api_url(profile)}/health").status == 200:
                return self.clock() - started
            if not self.status(profile).alive:
                raise ServerError(
                    "the server stopped while loading. Its last log lines:\n"
                    + self.logs(profile.name),
                    hint=f"Full log: {self.log_file(profile.name)}",
                )
            if self.clock() - started > timeout:
                raise ServerError(
                    f"the server was not ready after {timeout:.0f} s",
                    hint=f"See what it is doing: {self.log_command(profile)}",
                )
            if on_line is not None:
                line = _progress_line(self.logs(profile.name, 20))
                if line and line != last:
                    on_line(f"[{self.clock() - started:4.0f}s] {line}")
                    last = line
            self.sleep(POLL)

    def fit_check(self, profile: Profile) -> FitCheck | None:
        binary = self.tool(profile.runtime.image, FIT_PARAMS)
        if not binary.is_file():
            return None
        args = fit_params_args(native_config(profile, profile.runtime.port))[1:]
        result = self.host.run([str(binary), *args], timeout=300)
        if result is None or not result.ok:
            return None
        return parse_fit_params(result.stdout)


Runtime = Union[DockerRuntime, NativeRuntime]  # Docker on Linux, native on macOS
