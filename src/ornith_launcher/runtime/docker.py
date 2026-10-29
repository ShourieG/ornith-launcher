"""Running llama.cpp in Docker (Linux).

One container per profile, named ``ornith-launcher-<profile>`` and labelled with
the profile name and a hash of its arguments, so a later launch can tell
"already running as configured" (reuse it) from "running with other settings"
(refuse, and say how to stop it). The server is published on 127.0.0.1 only,
because the llama.cpp API has no authentication.

With the chat log on (``proxied``), the server is published on a free port Docker
picks, and the chat log proxy takes the profile's port. The launcher's own checks
(health, warm-up, metrics) always go straight to the server, on ``api_url``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ornith_launcher.profiles import Profile
from ornith_launcher.runtime.api import ServerApi, ServerError
from ornith_launcher.runtime.http import Http, UrllibHttp
from ornith_launcher.runtime.server import (
    CONTAINER_PORT,
    MODEL_DIR,
    TEMPLATE_DIR,
    ServerConfig,
    fit_params_args,
    server_args,
)
from ornith_launcher.system.host import Host, LineStream

PREFIX = "ornith-launcher-"
PROFILE_LABEL = "org.ornith-launcher.profile"
ARGS_LABEL = "org.ornith-launcher.args"
BOOT_TIMEOUT = 600.0
POLL = 3.0
# Images of other model servers that would compete for the same memory.
_OTHER_SERVERS = re.compile(r"vllm|sglang|llama|ollama|text-generation|tgi|trtllm|lmdeploy", re.I)


@dataclass(frozen=True)
class Container:
    name: str
    state: str  # "running", "restarting", "exited", "created", ... or "missing"
    image: str = ""
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def alive(self) -> bool:
        return self.state in ("running", "restarting")


@dataclass(frozen=True)
class FitCheck:
    """llama.cpp's own estimate, in MiB: [model, context, compute] per device."""

    devices: dict[str, list[int]]

    @property
    def total_mib(self) -> int:
        return sum(sum(values) for values in self.devices.values())


def digest_reference(reference: str) -> str:
    """ "repo:tag@sha256:..." -> "repo@sha256:...", the name Docker stored for a pinned pull."""
    name, at, digest = reference.partition("@")
    if not at:
        return reference
    last = name.rsplit("/", 1)[-1]
    repository = name.rsplit(":", 1)[0] if ":" in last else name
    return f"{repository}@{digest}"


def container_name(profile_name: str) -> str:
    return PREFIX + profile_name


def mounts(profile: Profile) -> list[tuple[str, str]]:
    """(host path, container path) for every file the server reads, mounted one by one.

    Mounting the real path (not a symlink in the Hugging Face cache) matters: a
    symlink pointing outside the mounted folder does not resolve in the container.
    Single files also keep the rest of the user's folder out of the container.
    """
    pairs = [(part, f"{MODEL_DIR}/{PurePosixPath(part).name}") for part in profile.model.parts]
    if profile.model.mmproj:
        pairs.append(
            (profile.model.mmproj, f"{MODEL_DIR}/{PurePosixPath(profile.model.mmproj).name}")
        )
    template = profile.runtime.template
    if template.source == "patched" and template.file:
        pairs.append((template.file, f"{TEMPLATE_DIR}/{PurePosixPath(template.file).name}"))
    return pairs


def server_config(profile: Profile) -> ServerConfig:
    runtime = profile.runtime
    container = dict(mounts(profile))
    template = runtime.template
    return ServerConfig(
        model=container[profile.model.parts[0]],
        alias=runtime.alias,
        context=runtime.context,
        slots=runtime.slots,
        kv_type=runtime.kv_type,
        native_context=runtime.native_context,
        yarn_factor=runtime.yarn_factor,
        mmproj=container.get(profile.model.mmproj) if profile.model.mmproj else None,
        template_file=container.get(template.file) if template.source == "patched" else None,
        cache_ram_mib=runtime.cache_ram_mib,
        sampling=runtime.sampling,
        direct_io=runtime.direct_io,
        cpu_moe=runtime.placement == "moe-offload",
        extra_args=tuple(runtime.extra_args),
    )


def _volume_args(profile: Profile) -> list[str]:
    args = []
    for host_path, container_path in mounts(profile):
        args += ["-v", f"{host_path}:{container_path}:ro"]
    return args


def _security_args(profile: Profile) -> list[str]:
    # SELinux blocks bind mounts of the user's files; never relabel them with :z.
    return ["--security-opt", "label=disable"] if profile.runtime.selinux else []


def run_args(profile: Profile, proxied: bool = False) -> list[str]:
    """The full ``docker run`` command for a profile (design 10.3).

    ``proxied``: publish on a port Docker picks, for the chat log proxy to forward to.
    """
    host_port = "" if proxied else str(profile.runtime.port)
    body = (
        ["--restart", "on-failure:3"]
        + list(profile.runtime.device_args)
        + _security_args(profile)
        + ["-p", f"127.0.0.1:{host_port}:{CONTAINER_PORT}"]
        + _volume_args(profile)
        + [profile.runtime.image]
        + server_args(server_config(profile))
    )
    digest = hashlib.sha256("\0".join(body).encode()).hexdigest()[:16]
    name = container_name(profile.name)
    labels = ["--label", f"{PROFILE_LABEL}={profile.name}", "--label", f"{ARGS_LABEL}={digest}"]
    return ["docker", "run", "-d", "--name", name, *labels, *body]


def args_hash(argv: Sequence[str]) -> str:
    for item in argv:
        if item.startswith(f"{ARGS_LABEL}="):
            return item.split("=", 1)[1]
    return ""


def fit_check_args(profile: Profile) -> list[str]:
    """``llama fit-params`` in the profile's image: llama.cpp's own memory estimate."""
    return (
        ["docker", "run", "--rm"]
        + list(profile.runtime.device_args)
        + _security_args(profile)
        + _volume_args(profile)
        + ["--entrypoint", "/app/llama", profile.runtime.image]
        + fit_params_args(server_config(profile))
    )


def parse_fit_params(text: str) -> FitCheck | None:
    """Lines such as ``CUDA0 34667 5182 757`` and ``Host 515 0 272``."""
    devices = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 4 and all(f.isdigit() for f in fields[1:]):
            devices[fields[0]] = [int(f) for f in fields[1:]]
    return FitCheck(devices) if devices else None


@dataclass
class DockerRuntime(ServerApi):
    host: Host
    http: Http = field(default_factory=UrllibHttp)
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    _ports: dict[str, int] = field(default_factory=dict)  # container -> port on 127.0.0.1

    # How the rest of the launcher names what this backend runs.
    kind = "docker"
    record_kind = "images"  # the key in installed.json
    state_column = "Container"
    server_noun = "llama.cpp image"
    fetched = "pulled"

    def describe(self, reference: str) -> str:
        return f"llama.cpp image {reference.split('@')[0]}"

    def download_size(self, reference: str) -> str:
        return "a few GB"

    def same_reference(self, one: str, other: str) -> bool:
        return digest_reference(one) == digest_reference(other)

    def command(self, profile: Profile) -> list[str]:
        """The full command that starts this profile's server (``status --command``)."""
        return run_args(profile)

    def log_command(self, profile: Profile) -> str:
        return f"docker logs {container_name(profile.name)}"

    # Images -----------------------------------------------------------------------------

    def image_present(self, reference: str) -> bool:
        result = self.host.run(["docker", "image", "inspect", reference], timeout=30)
        return result is not None and result.ok

    def pull(self, reference: str) -> None:
        if self.host.run_live(["docker", "pull", reference]) != 0:
            raise ServerError(
                f"docker could not pull {reference}",
                hint=f"Check your internet connection, then run: docker pull {reference}",
            )

    def remove_image(self, reference: str) -> tuple[str, str]:
        """Remove only the reference this launcher pulled, never the image's other tags.

        Returns ("deleted", ""), ("kept", "") when other tags still use the image, or
        ("failed", reason), for example while a container still uses it.
        """
        result = self.host.run(["docker", "image", "rm", digest_reference(reference)], timeout=120)
        if result is None:
            return "failed", "docker is not available"
        if not result.ok:
            return "failed", (result.stderr.strip().splitlines() or ["unknown error"])[0]
        return ("deleted" if "Deleted:" in result.stdout else "kept"), ""

    # Containers -------------------------------------------------------------------------

    def inspect(self, name: str) -> Container:
        result = self.host.run(["docker", "inspect", "--format", "{{json .}}", name], timeout=30)
        if result is None or not result.ok:
            return Container(name, "missing")
        try:
            info = json.loads(result.stdout)
        except ValueError:
            return Container(name, "missing")
        config = info.get("Config") or {}
        return Container(
            name=name,
            state=(info.get("State") or {}).get("Status", "unknown"),
            image=config.get("Image", ""),
            labels=dict(config.get("Labels") or {}),
        )

    def running_profiles(self) -> list[Container]:
        """Our containers that are running, whatever their profile."""
        result = self.host.run(
            ["docker", "ps", "--filter", f"label={PROFILE_LABEL}", "--format", "{{.Names}}"],
            timeout=30,
        )
        if result is None or not result.ok:
            return []
        return [self.inspect(name) for name in result.stdout.split() if name]

    def other_model_servers(self) -> list[str]:
        """Running containers of other model servers (vLLM, Ollama, ...), as "name (image)"."""
        result = self.host.run(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"], timeout=30)
        if result is None or not result.ok:
            return []
        found = []
        for line in result.stdout.splitlines():
            name, _, image = line.partition("\t")
            if name.startswith(PREFIX):
                continue
            if _OTHER_SERVERS.search(image) or _OTHER_SERVERS.search(name):
                found.append(f"{name} ({image})")
        return found

    def start(self, profile: Profile, proxied: bool = False) -> str:
        """Start the profile's server; "reused" if it already runs with the same settings."""
        argv = run_args(profile, proxied)
        name = container_name(profile.name)
        current = self.inspect(name)
        if current.alive:
            if current.labels.get(ARGS_LABEL) == args_hash(argv):
                return "reused"
            raise ServerError(
                f"{name} is already running with different settings"
                + (
                    " (the chat log is on for one and off for the other)"
                    if self._only_chat_log_differs(profile, current, proxied)
                    else ""
                ),
                hint=f"Stop it first: ornith-launcher stop {profile.name}",
            )
        if current.state != "missing":
            self.remove(name)  # a stopped container from an earlier run
        self._ports.pop(name, None)
        result = self.host.run(argv, timeout=120)
        if result is None or not result.ok:
            error = (result.stderr.strip() if result else "") or "docker run did not start"
            raise ServerError(
                f"the server did not start: {error.splitlines()[-1]}",
                hint=f"See the full command with: ornith-launcher status {profile.name} --verbose",
            )
        return "started"

    def stop(self, profile: Profile) -> bool:
        return self.remove(container_name(profile.name))

    def status(self, profile: Profile) -> Container:
        return self.inspect(container_name(profile.name))

    def remove(self, name: str) -> bool:
        """Stop and delete a container; its memory is released at once."""
        self._ports.pop(name, None)
        result = self.host.run(["docker", "rm", "-f", name], timeout=60)
        return result is not None and result.ok

    def _only_chat_log_differs(self, profile: Profile, current: Container, proxied: bool) -> bool:
        other = args_hash(run_args(profile, not proxied))
        return current.labels.get(ARGS_LABEL) == other

    def host_port(self, profile: Profile, cached: bool = True) -> int | None:
        """The port on 127.0.0.1 where the server itself listens, or None if not running.

        ``cached=False`` asks Docker again, e.g. after the container restarted.
        """
        name = container_name(profile.name)
        if cached and name in self._ports:
            return self._ports[name]
        result = self.host.run(["docker", "port", name, f"{CONTAINER_PORT}/tcp"], timeout=30)
        if result is None or not result.ok:
            return None
        for line in result.stdout.splitlines():
            host, _, port = line.strip().rpartition(":")
            if host == "127.0.0.1" and port.isdigit():
                self._ports[name] = int(port)
                return int(port)
        return None

    def logs(self, name: str, tail: int = 30) -> str:
        result = self.host.run(["docker", "logs", "--tail", str(tail), name], timeout=30)
        if result is None:
            return ""
        return (result.stdout + result.stderr).strip()

    def follow_logs(self, profile: Profile, tail: int) -> int:
        """Show the last ``tail`` log lines, then new ones until the server stops."""
        return self.host.run_live(
            ["docker", "logs", "--follow", "--tail", str(tail), container_name(profile.name)]
        )

    def log_stream(self, profile: Profile) -> LineStream | None:
        """New log lines from now on, for the server window; None if docker is missing."""
        return self.host.stream(
            ["docker", "logs", "--follow", "--tail", "0", container_name(profile.name)]
        )

    # Server -----------------------------------------------------------------------------

    def wait_ready(
        self,
        profile: Profile,
        on_line: Callable[[str], None] | None = None,
        timeout: float = BOOT_TIMEOUT,
    ) -> float:
        """Wait until /health answers 200; returns the seconds it took."""
        name = container_name(profile.name)
        started = self.clock()
        last = ""
        while True:
            if self.http.get(f"{self.api_url(profile)}/health").status == 200:
                return self.clock() - started
            container = self.inspect(name)
            if not container.alive:
                raise ServerError(
                    f"the server stopped while loading. Its last log lines:\n{self.logs(name)}",
                    hint=f"Full log: docker logs {name}",
                )
            if self.clock() - started > timeout:
                raise ServerError(
                    f"the server was not ready after {timeout:.0f} s",
                    hint=f"See what it is doing: docker logs {name}",
                )
            if on_line is not None:
                line = _progress_line(self.logs(name, 20))
                if line and line != last:
                    on_line(f"[{self.clock() - started:4.0f}s] {line}")
                    last = line
            self.sleep(POLL)

    def fit_check(self, profile: Profile) -> FitCheck | None:
        result = self.host.run(fit_check_args(profile), timeout=300)
        if result is None or not result.ok:
            return None
        return parse_fit_params(result.stdout)


def _progress_line(logs: str) -> str:
    keywords = ("load", "model", "KV", "context", "server", "slot")
    lines = [line for line in logs.splitlines() if any(k in line for k in keywords)]
    return lines[-1][:110] if lines else ""
