"""Ornith releases (version + size) and the GGUF shapes that identify each size.

The data lives in ``data/families.json`` so a new Ornith version with a known
architecture needs a data change only, not a code change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ornith_launcher.catalog.arch import ModelArch
from ornith_launcher.errors import DataFileError
from ornith_launcher.resources import load_json

FAMILIES_FILE = "families.json"
YARN_STATES = ("documented", "undocumented")


@dataclass(frozen=True)
class Shape:
    """Header fingerprint of one size, e.g. 35B: qwen35moe, 2048 wide, 40 layers, 256 experts."""

    id: str
    label: str
    architecture: str
    embedding_length: int
    main_layers: int
    expert_count: int

    def matches(self, arch: ModelArch) -> bool:
        return (
            arch.architecture == self.architecture
            and arch.embedding_length == self.embedding_length
            and arch.main_layers == self.main_layers
            and arch.expert_count == self.expert_count
        )


@dataclass(frozen=True)
class Family:
    """One Ornith release at one size, e.g. Ornith 1.5 35B-A3B."""

    version: str
    size: str
    shape: str
    official_repos: tuple[str, ...]
    base_repos: tuple[str, ...]
    aliases: tuple[str, ...]  # old names of base repos (the org was called deepreinforce-ai)
    native_ctx: int
    yarn: str  # "documented" if the model card validates YaRN long context
    untested_locally: bool  # shows the UNTESTED LOCALLY badge
    sampling: Mapping[str, float] = field(default_factory=dict, compare=False)

    @property
    def display_name(self) -> str:
        return f"Ornith {self.version} {self.size}"

    @property
    def lineage_repos(self) -> tuple[str, ...]:
        """Repos a community build may name as its ``base_model``."""
        return self.base_repos + self.aliases


@dataclass(frozen=True)
class Families:
    official_owners: tuple[str, ...]
    shapes: Mapping[str, Shape]
    families: tuple[Family, ...]

    @property
    def versions(self) -> tuple[str, ...]:
        """Known versions, newest first."""
        return tuple(sorted({f.version for f in self.families}, key=version_key, reverse=True))

    def match_shape(self, arch: ModelArch) -> Shape | None:
        return next((s for s in self.shapes.values() if s.matches(arch)), None)

    def find(self, version: str, shape_id: str) -> Family | None:
        return next(
            (f for f in self.families if f.version == version and f.shape == shape_id), None
        )

    def by_official_repo(self, repo_id: str) -> Family | None:
        wanted = repo_id.lower()
        return next(
            (f for f in self.families if wanted in (r.lower() for r in f.official_repos)), None
        )

    def by_lineage_repo(self, repo_id: str) -> Family | None:
        wanted = repo_id.lower()
        return next(
            (f for f in self.families if wanted in (r.lower() for r in f.lineage_repos)), None
        )

    def is_official_owner(self, repo_id: str) -> bool:
        owner = repo_id.split("/", 1)[0].lower()
        return owner in (o.lower() for o in self.official_owners)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Families:
        """Build and validate from the parsed JSON; raises DataFileError on bad data."""
        try:
            shapes = {
                shape_id: Shape(
                    id=shape_id,
                    label=str(raw["label"]),
                    architecture=str(raw["architecture"]),
                    embedding_length=int(raw["embedding_length"]),
                    main_layers=int(raw["main_layers"]),
                    expert_count=int(raw["expert_count"]),
                )
                for shape_id, raw in data["shapes"].items()
            }
            families = tuple(_family_from_dict(raw) for raw in data["families"])
            owners = tuple(str(owner) for owner in data["official_owners"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataFileError(f"{FAMILIES_FILE} is malformed: {exc!r}") from exc

        seen: set[tuple[str, str]] = set()
        for family in families:
            if not all(part.isdigit() for part in family.version.split(".")):
                raise DataFileError(f"{family.display_name} has a non-numeric version")
            if family.shape not in shapes:
                raise DataFileError(f"{family.display_name} uses unknown shape {family.shape!r}")
            if family.yarn not in YARN_STATES:
                raise DataFileError(f"{family.display_name} has invalid yarn {family.yarn!r}")
            key = (family.version, family.shape)
            if key in seen:
                raise DataFileError(f"{family.display_name} is listed twice")
            seen.add(key)
        return cls(official_owners=owners, shapes=shapes, families=families)


def _family_from_dict(raw: Mapping[str, Any]) -> Family:
    return Family(
        version=str(raw["version"]),
        size=str(raw["size"]),
        shape=str(raw["shape"]),
        official_repos=tuple(raw["official_repos"]),
        base_repos=tuple(raw["base_repos"]),
        aliases=tuple(raw["aliases"]),
        native_ctx=int(raw["native_ctx"]),
        yarn=str(raw["yarn"]),
        untested_locally=bool(raw["untested_locally"]),
        sampling=dict(raw["sampling"]),
    )


def version_key(version: str) -> tuple[int, ...]:
    """Sort key for versions: "1.10" sorts after "1.9"."""
    return tuple(int(part) for part in version.split("."))


def load_families() -> Families:
    """Load the families shipped with the package."""
    return Families.from_dict(load_json(FAMILIES_FILE))
