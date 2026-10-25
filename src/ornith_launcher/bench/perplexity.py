"""Short-context quality: ``llama perplexity`` on local prose, in the profile's image."""

from __future__ import annotations

import re
from pathlib import Path

from ornith_launcher.profiles import Profile
from ornith_launcher.runtime.docker import mounts
from ornith_launcher.runtime.server import yarn_args

CONTEXT = 4096
_FINAL = re.compile(r"Final estimate: PPL = ([0-9.]+) \+/- ([0-9.]+)")


def perplexity_args(profile: Profile, text: Path, yarn_factor: int | None = None) -> list[str]:
    """``docker run`` for llama perplexity with the profile's model and image."""
    volumes = []
    for host_path, container_path in mounts(profile):
        volumes += ["-v", f"{host_path}:{container_path}:ro"]
    volumes += ["-v", f"{text}:/work/ppl.txt:ro"]
    security = ["--security-opt", "label=disable"] if profile.runtime.selinux else []
    model = dict(mounts(profile))[profile.model.parts[0]]
    args = ["docker", "run", "--rm", *profile.runtime.device_args, *security, *volumes]
    args += ["--entrypoint", "/app/llama", profile.runtime.image, "perplexity", "-m", model]
    args += ["-f", "/work/ppl.txt", "-c", str(CONTEXT), "-b", str(CONTEXT), "-ngl", "999"]
    args += ["-fa", "on"]
    if profile.runtime.direct_io:
        args += ["--load-mode", "dio"]
    return args + yarn_args(yarn_factor, profile.runtime.native_context)


def parse_perplexity(log: str) -> tuple[float, float] | None:
    """(perplexity, error) from llama's final line, or None if it did not finish."""
    match = _FINAL.search(log)
    return (float(match.group(1)), float(match.group(2))) if match else None
