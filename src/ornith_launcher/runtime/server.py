"""llama-server arguments, shared by every runtime backend.

The flags are the form tested on the DGX Spark with llama.cpp b11552. Some are
build-specific (``--load-mode`` replaced ``--no-mmap`` in that build), so a pin
change in ``data/pins.json`` must be checked against this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

CONTAINER_PORT = 8080
MODEL_DIR = "/models"
TEMPLATE_DIR = "/templates"


@dataclass(frozen=True)
class ServerConfig:
    model: str  # path as the server sees it (first part of a split model)
    alias: str  # the model name clients ask for
    context: int
    slots: int
    kv_type: str = "f16"
    native_context: int = 262144
    yarn_factor: int | None = None  # 2 or 4 for contexts beyond native
    mmproj: str | None = None
    template_file: str | None = None  # a patched chat template (Claude Code)
    cache_ram_mib: int = 4096
    sampling: Mapping[str, float] = field(default_factory=dict)
    host: str = "0.0.0.0"  # inside the container; Docker publishes it on 127.0.0.1 only
    port: int = CONTAINER_PORT
    gpu_layers: int = 999
    direct_io: bool = True  # --load-mode dio: no second page-cache copy on unified memory
    cpu_moe: bool = False  # experts in system RAM
    extra_args: Sequence[str] = ()


def server_args(config: ServerConfig) -> list[str]:
    args = ["-m", config.model]
    if config.mmproj:
        args += ["--mmproj", config.mmproj]
    args += ["--alias", config.alias, "--host", config.host, "--port", str(config.port)]
    args += ["-ngl", str(config.gpu_layers), "-fa", "on"]
    if config.direct_io:
        args += ["--load-mode", "dio"]
    # Explicit values plus --fit off, so the server runs exactly the plan that was checked.
    args += ["--fit", "off"]
    if config.cpu_moe:
        args += ["--cpu-moe"]
    args += ["-c", str(config.context), "-np", str(config.slots), "--kv-unified"]
    args += ["-ctk", config.kv_type, "-ctv", config.kv_type]
    args += yarn_args(config.yarn_factor, config.native_context)
    args += ["--cache-ram", str(config.cache_ram_mib), "--jinja"]
    if config.template_file:
        args += ["--chat-template-file", config.template_file]
    args += ["--reasoning-format", "auto"]
    for flag, key in (("--temp", "temperature"), ("--top-p", "top_p"), ("--top-k", "top_k")):
        if key in config.sampling:
            args += [flag, _number(config.sampling[key])]
    args += ["--metrics"]
    args += list(config.extra_args)
    return args


def yarn_args(factor: int | None, native_context: int) -> list[str]:
    """YaRN flags for a context beyond native; none when the file brings its own scaling."""
    if not factor:
        return []
    return [
        "--rope-scaling",
        "yarn",
        "--rope-scale",
        str(factor),
        "--yarn-orig-ctx",
        str(native_context),
    ]


def fit_params_args(config: ServerConfig) -> list[str]:
    """Arguments for ``llama fit-params``: the same model settings, minus server-only flags."""
    args = ["fit-params", "-m", config.model, "-c", str(config.context), "-fa", "on"]
    args += ["-ngl", str(config.gpu_layers)]
    if config.cpu_moe:
        args += ["--cpu-moe"]
    if config.kv_type != "f16":
        args += ["-ctk", config.kv_type, "-ctv", config.kv_type]
    args += yarn_args(config.yarn_factor, config.native_context)
    # --kv-unified is a server flag that fit-params rejects.
    return args + ["--fit-print", "on"]


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)
