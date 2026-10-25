"""Profiles: one saved, launchable setup each (``profiles/<name>.json`` in the config folder).

A profile records everything ``launch`` needs, so launching never re-runs setup
steps: the model file and where it came from, the exact runtime settings and
image, the plan's memory estimate, and every file created outside the tool's
own folders, so ``remove`` and ``uninstall`` can undo it exactly.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ornith_launcher.config import Paths, atomic_write_json, read_json
from ornith_launcher.errors import OrnithLauncherError

SCHEMA_VERSION = 1
_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{0,62}$")


class ProfileError(OrnithLauncherError):
    pass


@dataclass
class ModelInfo:
    path: str  # real path of the first (or only) part; symlinks resolved
    parts: list[str]  # real paths of every part
    size: int
    mtime_ns: int
    location: str  # "managed" (downloaded by us) or "external" (found or entered; never deleted)
    repo: str | None = None
    sha: str | None = None
    file: str | None = None
    owner_app: str | None = None
    sha256: str | None = None
    mmproj: str | None = None  # real path of the vision projector, if vision is on
    version: str | None = None
    size_class: str | None = None
    quant: str | None = None
    source: str = "official"
    uncensored: bool = False
    licence: str | None = None


@dataclass
class TemplateInfo:
    source: str = "gguf"  # "gguf" (the model's own) or "patched"
    rule: str | None = None
    sha256: str | None = None  # of the model's original template
    file: str | None = None  # the patched copy, when source is "patched"


@dataclass
class RuntimeInfo:
    image: str  # reference with digest
    flavour: str
    device_args: list[str]
    context: int
    native_context: int
    slots: int
    kv_type: str
    port: int
    alias: str
    yarn_factor: int | None = None
    cache_ram_mib: int = 4096
    placement: str = "single"
    direct_io: bool = True
    sampling: dict[str, float] = field(default_factory=dict)
    selinux: bool = False
    extra_args: list[str] = field(default_factory=list)
    template: TemplateInfo = field(default_factory=TemplateInfo)


@dataclass
class PlanInfo:
    estimate_gib: float
    level: str
    fitcheck: dict[str, list[int]] | None = None  # "device"/"host": [model, context, compute] MiB


@dataclass
class Profile:
    name: str
    created: str
    launcher_version: str
    model: ModelInfo
    runtime: RuntimeInfo
    plan: PlanInfo
    integrations: dict[str, Any] = field(default_factory=dict)
    terminal: dict[str, Any] = field(default_factory=dict)
    consents: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.runtime.port}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data = {"schema_version": data.pop("schema_version"), **data}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ProfileError(f"profile schema {data.get('schema_version')!r} is not supported")
        try:
            runtime = dict(data["runtime"])
            runtime["template"] = TemplateInfo(**runtime.get("template", {}))
            return cls(
                name=data["name"],
                created=data["created"],
                launcher_version=data["launcher_version"],
                model=ModelInfo(**data["model"]),
                runtime=RuntimeInfo(**runtime),
                plan=PlanInfo(**data["plan"]),
                integrations=dict(data.get("integrations") or {}),
                terminal=dict(data.get("terminal") or {}),
                consents=dict(data.get("consents") or {}),
            )
        except (KeyError, TypeError) as exc:
            raise ProfileError(f"profile {data.get('name')!r} is malformed: {exc}") from exc


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name))


def profile_path(paths: Paths, name: str) -> Path:
    if not valid_name(name):
        raise ProfileError(
            f"{name!r} is not a valid profile name",
            hint="Use lower-case letters, digits, dots and dashes, e.g. ornith-35b-q8.",
        )
    return paths.profiles / f"{name}.json"


def save_profile(paths: Paths, profile: Profile) -> Path:
    path = profile_path(paths, profile.name)
    atomic_write_json(path, profile.to_dict())
    return path


def load_profile(paths: Paths, name: str) -> Profile:
    path = profile_path(paths, name)
    try:
        data = read_json(path)
    except ValueError as exc:
        raise ProfileError(f"{path} is not valid JSON ({exc})") from exc
    if data is None:
        known = list_profiles(paths)
        hint = (
            f"Known profiles: {', '.join(known)}"
            if known
            else "Create one with: ornith-launcher setup"
        )
        raise ProfileError(f"there is no profile named {name!r}", hint=hint)
    return Profile.from_dict(data)


def list_profiles(paths: Paths) -> list[str]:
    if not paths.profiles.is_dir():
        return []
    return sorted(p.stem for p in paths.profiles.glob("*.json") if valid_name(p.stem))


def delete_profile(paths: Paths, name: str) -> None:
    profile_path(paths, name).unlink(missing_ok=True)


def suggest_name(
    version: str | None,
    size_class: str | None,
    quant: str | None,
    context: int,
    existing: list[str],
) -> str:
    """A readable unique name such as ``ornith-1.5-35b-q8-262k``."""
    parts = ["ornith"]
    if version:
        parts.append(version)
    if size_class:
        parts.append(size_class.split("-")[0].lower())
    if quant:
        parts.append(quant.lower().replace("_k_m", "").replace("_0", "").replace("_", ""))
    parts.append(
        f"{round(context / 1000)}k" if context < 1_000_000 else f"{round(context / 1_048_576)}m"
    )
    base = "-".join(parts)
    name, counter = base, 2
    while name in existing:
        name = f"{base}-{counter}"
        counter += 1
    return name
