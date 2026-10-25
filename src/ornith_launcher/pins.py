"""The pinned llama.cpp build: Docker images by digest (``data/pins.json``).

Changing a pin needs review and a re-run of the validation matrix, so the pins
live in data rather than code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ornith_launcher.errors import DataFileError
from ornith_launcher.resources import load_json

PINS_FILE = "pins.json"


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
class Pins:
    build: str
    images: Mapping[str, DockerImage]

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
        except (KeyError, TypeError) as exc:
            raise DataFileError(f"{PINS_FILE} is malformed: {exc!r}") from exc
        for image in images.values():
            if not image.digest.startswith("sha256:"):
                raise DataFileError(f"{PINS_FILE}: image {image.tag} is not pinned by digest")
        return cls(build=build, images=images)


def load_pins() -> Pins:
    return Pins.from_dict(load_json(PINS_FILE))
