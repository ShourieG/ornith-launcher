"""Classify one GGUF file: which Ornith family it belongs to, where it comes from, and its badges.

The size comes from the header shape. The version cannot, because Ornith 1.0 and
1.5 share their shapes, so it comes from the first available of: the official
repo, the declared base model (lineage), the header's name, the repo name, and
the file name.
"""

from __future__ import annotations

import enum
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ornith_launcher.catalog.arch import ModelArch
from ornith_launcher.catalog.families import Families, Family, Shape
from ornith_launcher.catalog.gguf import GGUFError
from ornith_launcher.catalog.quant import is_low_bit, quant_from_file_type, quant_from_name
from ornith_launcher.errors import DataFileError
from ornith_launcher.resources import load_json

logger = logging.getLogger(__name__)

CLASSIFICATION_FILE = "classification.json"

# Matches "Ornith-1.5", "ornith_1_0", "Ornith 1.0 9B", "deepreinforce-ai_Ornith-1.0-397B".
_VERSION_IN_NAME = re.compile(r"ornith[\s._-]*v?(\d+)[._-](\d+)(?!\d)", re.IGNORECASE)

# Header keys that name the model, most specific first.
_HEADER_NAME_KEYS = ("general.basename", "general.name")
# Header keys that say what a file was made from, for the uncensored check:
# llama.cpp's converter copies them from the model card, so files found on disk
# without a repo still carry them.
_HEADER_LINEAGE_KEYS = ("general.finetune", "general.tags")
_HEADER_BASE_MODEL = re.compile(r"general\.base_model\.\d+\.(name|repo_url)")


class Source(str, enum.Enum):
    OFFICIAL = "official"
    COMMUNITY = "community"
    LOCAL = "local"  # a file on disk with no Hugging Face repo


class VersionSource(str, enum.Enum):
    """Where the version came from, in order of trust."""

    OFFICIAL_REPO = "official repo"
    LINEAGE = "base model"
    HEADER = "file header"
    REPO_NAME = "repo name"
    FILE_NAME = "file name"


class Badge(str, enum.Enum):
    UNTESTED_LOCALLY = "UNTESTED LOCALLY"
    LOW_BIT = "LOW-BIT"
    VERSION_UNKNOWN = "VERSION UNKNOWN"
    UNCENSORED = "UNCENSORED"


@dataclass(frozen=True)
class Rules:
    """Keyword lists from ``data/classification.json``."""

    uncensored_keywords: tuple[str, ...]
    low_bit_prefixes: tuple[str, ...]


def load_rules() -> Rules:
    data = load_json(CLASSIFICATION_FILE)
    try:
        return Rules(
            uncensored_keywords=tuple(str(k).lower() for k in data["uncensored_keywords"]),
            low_bit_prefixes=tuple(str(p) for p in data["low_bit_prefixes"]),
        )
    except (KeyError, TypeError) as exc:
        raise DataFileError(f"{CLASSIFICATION_FILE} is malformed: {exc!r}") from exc


@dataclass(frozen=True)
class FileEvidence:
    """What is known about a GGUF file apart from its header.

    ``repo_id`` ("owner/name") is None for a file found on disk. ``base_models``
    and ``tags`` come from the Hugging Face repo metadata.
    """

    file_name: str
    repo_id: str | None = None
    base_models: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Classification:
    shape: Shape | None
    family: Family | None
    version: str | None
    version_source: VersionSource | None
    source: Source
    lineage_verified: bool  # the repo declares an Ornith base model
    quant: str | None
    low_bit: bool
    uncensored: bool
    excluded: str | None  # why the file is not offered; None for a supported model

    @property
    def supported(self) -> bool:
        return self.excluded is None

    @property
    def size_label(self) -> str | None:
        if self.family is not None:
            return self.family.size  # e.g. "35B-A3B" for 1.5, "35B" for 1.0
        return self.shape.label if self.shape is not None else None

    @property
    def badges(self) -> tuple[Badge, ...]:
        badges = []
        if self.family is not None and self.family.untested_locally:
            badges.append(Badge.UNTESTED_LOCALLY)
        if self.low_bit:
            badges.append(Badge.LOW_BIT)
        if self.supported and self.version is None:
            badges.append(Badge.VERSION_UNKNOWN)
        if self.uncensored:
            badges.append(Badge.UNCENSORED)
        return tuple(badges)


def classify(
    metadata: Mapping[str, Any], evidence: FileEvidence, families: Families, rules: Rules
) -> Classification:
    """Classify one file from its parsed GGUF metadata and what is known about its repo."""
    quant = quant_from_name(evidence.file_name) or quant_from_file_type(
        metadata.get("general.file_type")
    )
    lineage_family = _first_lineage_family(evidence.base_models, families)
    common = dict(
        source=_source(evidence.repo_id, families),
        lineage_verified=lineage_family is not None,
        quant=quant,
        low_bit=is_low_bit(quant, rules.low_bit_prefixes),
        uncensored=_is_uncensored(evidence, metadata, rules.uncensored_keywords),
    )

    try:
        arch = ModelArch.from_metadata(metadata)
    except GGUFError as exc:
        return Classification(
            shape=None,
            family=None,
            version=None,
            version_source=None,
            excluded=f"header is not a recognised model: {exc}",
            **common,
        )

    shape = families.match_shape(arch)
    if shape is None:
        reason = (
            f"unsupported shape: {arch.architecture}, width {arch.embedding_length}, "
            f"{arch.main_layers} layers, {arch.expert_count} experts"
        )
        logger.debug("excluding %s: %s", evidence.file_name, reason)
        return Classification(
            shape=None, family=None, version=None, version_source=None, excluded=reason, **common
        )

    version, version_source = detect_version(evidence, metadata, families, lineage_family)
    family = families.find(version, shape.id) if version is not None else None
    excluded = None
    if version is not None and family is None:
        excluded = f"no {shape.label} model is known for Ornith {version}"
    return Classification(
        shape=shape,
        family=family,
        version=version,
        version_source=version_source,
        excluded=excluded,
        **common,
    )


def detect_version(
    evidence: FileEvidence,
    metadata: Mapping[str, Any],
    families: Families,
    lineage_family: Family | None = None,
) -> tuple[str | None, VersionSource | None]:
    """Return the version and where it came from, or (None, None) if nothing names one.

    Every source is checked so that disagreements can be logged; the most trusted wins.
    """
    known = set(families.versions)
    found: list[tuple[VersionSource, str]] = []

    def add(source: VersionSource, version: str | None) -> None:
        if version is None:
            return
        if version in known:
            found.append((source, version))
        else:
            logger.info("%s names unknown Ornith version %s", evidence.file_name, version)

    repo_name = evidence.repo_id.split("/", 1)[-1] if evidence.repo_id else None
    if evidence.repo_id and families.is_official_owner(evidence.repo_id):
        official = families.by_official_repo(evidence.repo_id)
        add(VersionSource.OFFICIAL_REPO, official.version if official else _version_in(repo_name))
    if lineage_family is not None:
        add(VersionSource.LINEAGE, lineage_family.version)
    for key in _HEADER_NAME_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and _version_in(value):
            add(VersionSource.HEADER, _version_in(value))
            break
    if repo_name:
        add(VersionSource.REPO_NAME, _version_in(repo_name))
    add(VersionSource.FILE_NAME, _version_in(evidence.file_name))

    if not found:
        return None, None
    source, version = found[0]
    disagreeing = [(s.value, v) for s, v in found[1:] if v != version]
    if disagreeing:
        logger.info(
            "%s: using version %s from %s; other sources say %s",
            evidence.file_name,
            version,
            source.value,
            disagreeing,
        )
    return version, source


def _version_in(text: str | None) -> str | None:
    if not text:
        return None
    match = _VERSION_IN_NAME.search(text)
    return f"{int(match.group(1))}.{int(match.group(2))}" if match else None


def _first_lineage_family(base_models: Iterable[str], families: Families) -> Family | None:
    for base in base_models:
        family = families.by_lineage_repo(base)
        if family is not None:
            return family
    return None


def _source(repo_id: str | None, families: Families) -> Source:
    if repo_id is None:
        return Source.LOCAL
    return Source.OFFICIAL if families.is_official_owner(repo_id) else Source.COMMUNITY


def _is_uncensored(
    evidence: FileEvidence, metadata: Mapping[str, Any], keywords: Iterable[str]
) -> bool:
    texts = [evidence.file_name, evidence.repo_id or "", *evidence.tags, *evidence.base_models]
    for key, value in metadata.items():
        if key in _HEADER_NAME_KEYS + _HEADER_LINEAGE_KEYS or _HEADER_BASE_MODEL.fullmatch(key):
            values = value if isinstance(value, (list, tuple)) else [value]
            texts += [v for v in values if isinstance(v, str)]
    haystack = " ".join(texts).lower()
    return any(keyword in haystack for keyword in keywords)
