"""A profile and fake Docker/HTTP for runtime tests."""

from __future__ import annotations

import json
from pathlib import Path

from ornith_launcher.profiles import ModelInfo, PlanInfo, Profile, RuntimeInfo, TemplateInfo
from ornith_launcher.runtime.http import Reply
from ornith_launcher.system.host import CommandResult
from tests.fake_host import FakeHost

IMAGE = "ghcr.io/ggml-org/llama.cpp:server-cuda13-b11552@sha256:" + "4" * 64


def profile(tmp: Path, name: str = "ornith-1.5-35b-q8-262k", **runtime) -> Profile:
    model = tmp / "Ornith-1.5-35B-Q8_0.gguf"
    if not model.exists():
        model.write_bytes(b"GGUF")
    settings = dict(
        image=IMAGE,
        flavour="cuda13",
        device_args=["--gpus", "all"],
        context=262144,
        native_context=262144,
        slots=4,
        kv_type="f16",
        port=18400,
        alias="ornith-1.5-35b-q8_0",
        sampling={"temperature": 0.6, "top_p": 0.95, "top_k": 20},
    )
    settings.update(runtime)
    return Profile(
        name=name,
        created="2026-10-25T00:00:00+00:00",
        launcher_version="0.1.0.dev0",
        model=ModelInfo(
            path=str(model),
            parts=[str(model)],
            size=model.stat().st_size,
            mtime_ns=model.stat().st_mtime_ns,
            location="external",
            repo="ornith-ai/Ornith-1.5-35B-A3B-GGUF",
            version="1.5",
            size_class="35B-A3B",
            quant="Q8_0",
        ),
        runtime=RuntimeInfo(template=TemplateInfo(), **settings),
        plan=PlanInfo(estimate_gib=46.6, level="INFERRED"),
    )


class FakeDocker(FakeHost):
    """Answers docker commands from a small in-memory model of containers and images."""

    def __init__(self, **kwargs):
        kwargs.setdefault("programs", {"docker": "/usr/bin/docker"})
        super().__init__(**kwargs)
        self.containers: dict[str, dict] = {}
        self.images = {IMAGE}
        self.other = ""  # extra lines for docker ps
        self.run_fails = False
        self.fit_output = "CUDA0 34667 5182 757\nHost 515 0 272\n"

    def run(self, argv, timeout=10.0):
        if argv[0] != "docker":
            return super().run(argv, timeout)
        self.calls.append(tuple(argv))
        args = list(argv)
        if args[:3] == ["docker", "image", "inspect"]:
            return CommandResult(0 if args[3] in self.images else 1, "", "")
        if args[:2] == ["docker", "inspect"]:
            info = self.containers.get(args[-1])
            if info is None:
                return CommandResult(1, "", "No such object")
            return CommandResult(0, json.dumps(info), "")
        if args[:2] == ["docker", "ps"]:
            if "--filter" in args:
                names = [n for n, c in self.containers.items() if c["State"]["Status"] == "running"]
                return CommandResult(0, "\n".join(names), "")
            lines = [f"{n}\t{c['Config']['Image']}" for n, c in self.containers.items()]
            return CommandResult(0, "\n".join(lines + ([self.other] if self.other else [])), "")
        if args[:3] == ["docker", "rm", "-f"]:
            self.containers.pop(args[3], None)
            return CommandResult(0, "", "")
        if args[:3] == ["docker", "run", "-d"]:
            if self.run_fails:
                return CommandResult(125, "", "docker: Error response from daemon: boom")
            name = args[args.index("--name") + 1]
            labels = dict(args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "--label")
            self.containers[name] = {
                "State": {"Status": "running"},
                "Config": {"Image": IMAGE, "Labels": labels},
            }
            return CommandResult(0, "abc123\n", "")
        if args[:3] == ["docker", "run", "--rm"]:
            return CommandResult(0, self.fit_output, "")
        if args[:2] == ["docker", "logs"]:
            return CommandResult(0, "llama_model_load: loading model\nserver is listening\n", "")
        return CommandResult(127, "", "unexpected")


class FakeHttp:
    def __init__(self):
        self.health = [200]
        self.props = {"default_generation_settings": {"n_ctx": 262144}}
        self.models = {"data": [{"id": "ornith-1.5-35b-q8_0"}]}
        self.metrics = "llamacpp:requests_processing 2\n"
        self.posts = []
        self.chat_status = 200

    def get(self, url, timeout=3.0):
        if url.endswith("/health"):
            status = self.health.pop(0) if len(self.health) > 1 else self.health[0]
            return Reply(status, b"{}")
        if url.endswith("/props"):
            return Reply(200, json.dumps(self.props).encode())
        if url.endswith("/v1/models"):
            return Reply(200, json.dumps(self.models).encode())
        if url.endswith("/metrics"):
            return Reply(200, self.metrics.encode())
        return Reply(404, b"")

    def post_json(self, url, payload, timeout=300.0):
        self.posts.append((url, payload))
        return Reply(self.chat_status, b'{"choices": []}')
