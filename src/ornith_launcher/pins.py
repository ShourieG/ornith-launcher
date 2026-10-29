"""The pinned llama.cpp build: Docker images by digest and release archives by sha256
(``data/pins.json``).

Changing a pin needs review and a re-run of the validation matrix, so the pins
live in data rather than code.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ornith_launcher.errors import DataFileError
from ornith_launcher.resources import load_json

PINS_FILE = "pins.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class DockerImage:
    flavour: str  # "cpu", "cuda", "cuda13", "rocm", "vulkan" or "intel"
    repository: str
    tag: str
    digest: str
    platforms: tuple[str, ...]  # e.g. "linux/arm64"
    tested: bool

    @property
    def reference(self) -> str:
        """Image reference that Docker resolves by digest, so the tag cannot move under us."""
        return f"{self.repository}:{self.tag}@{self.digest}"

    def supports(self, arch: str) -> bool:
        return f"linux/{arch}" in self.platforms


@dataclass(frozen=True)
class NativeAsset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class NativeBuild:
    """A llama.cpp release archive for running without Docker (macOS; Windows later)."""

    key: str  # e.g. "macos-arm64"
    build: str  # e.g. "b11552"
    assets: tuple[NativeAsset, ...]

    @property
    def reference(self) -> str:
        """The folder the archive is unpacked into, and the profile's runtime reference."""
        return f"llama-{self.build}-{self.key}"

    @property
    def size(self) -> int:
        return sum(asset.size for asset in self.assets)


@dataclass(frozen=True)
class Pins:
    build: str
    images: Mapping[str, DockerImage]
    native: Mapping[str, NativeBuild] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Pins:
        try:
            docker = data["docker"]
            images = {
                name: DockerImage(
                    flavour=name,
                    repository=str(docker["image"]),
                    tag=str(raw["tag"]),
                    digest=str(raw["digest"]),
                    platforms=tuple(raw["platforms"]),
                    tested=raw["status"] == "tested",
                )
                for name, raw in docker["flavours"].items()
            }
            build = str(data["llama_cpp"]["build"])
            native_data = data.get("native") or {}
            base_url = str(native_data.get("base_url", ""))
            native = {
                key: NativeBuild(
                    key=key,
                    build=build,
                    assets=tuple(
                        NativeAsset(
                            name=str(raw["name"]),
                            url=base_url + str(raw["name"]),
                            size=int(raw["size"]),
                            sha256=str(raw["sha256"]),
                        )
                        for raw in assets
                    ),
                )
                for key, assets in (native_data.get("builds") or {}).items()
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise DataFileError(f"{PINS_FILE} is malformed: {exc!r}") from exc
        for image in images.values():
            if not image.digest.startswith("sha256:"):
                raise DataFileError(f"{PINS_FILE}: image {image.tag} is not pinned by digest")
        for native_build in native.values():
            for asset in native_build.assets:
                if not _SHA256.match(asset.sha256) or not asset.url.startswith("https://"):
                    raise DataFileError(
                        f"{PINS_FILE}: {asset.name} is not pinned by sha256 over https"
                    )
        return cls(build=build, images=images, native=native)


def load_pins() -> Pins:
    return Pins.from_dict(load_json(PINS_FILE))
