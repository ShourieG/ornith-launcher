"""The catalog of Ornith GGUF builds on Hugging Face: discovery, caching and lookup.

Discovery is the union of a name search and lineage queries (repos that declare
an Ornith base model); neither finds everything alone. Each repo's file list and
the header of one of its GGUF files are stored, and classification happens when
the catalog is loaded, so a change to ``families.json`` applies without a rescan.

A seed catalog ships in ``data/catalog-seed.json`` so the first run works
offline; ``scan --refresh`` rebuilds it into the cache folder, re-reading only
repos whose revision changed.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from ornith_launcher.catalog import gguf
from ornith_launcher.catalog.arch import ModelArch
from ornith_launcher.catalog.classify import Classification, FileEvidence, Rules, classify
from ornith_launcher.catalog.families import Families
from ornith_launcher.catalog.gguf import ArraySummary, GGUFError, NeedMoreBytes
from ornith_launcher.catalog.hfapi import HubClient, HubError
from ornith_launcher.catalog.quant import is_low_bit, quant_from_name
from ornith_launcher.config import atomic_write_json, read_json
from ornith_launcher.errors import DataFileError
from ornith_launcher.resources import load_json

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SEED_FILE = "catalog-seed.json"
REFRESH_SECONDS = 24 * 3600
LINEAGE_RELATIONS = ("", "quantized:", "finetune:", "merge:", "adapter:")

# "model-00001-of-00003.gguf": the first part holds the header.
_SPLIT = re.compile(r"^(?P<stem>.+)-(?P<part>\d{5})-of-(?P<total>\d{5})\.gguf$", re.IGNORECASE)
_MAX_HEADER_STRING = 200
# The smallest real quants (IQ1_S) use about 1.56 bits per weight; less means a partial file.
MIN_BITS_PER_WEIGHT = 1.4
_PARTIAL_MARKERS = ("mtp-head", "-head-", "/draft", "draft-", "-draft")
_HEADER_GENERAL_KEYS = (
    "general.architecture",
    "general.name",
    "general.basename",
    "general.file_type",
    "general.size_label",
)


@dataclass(frozen=True)
class FilePart:
    path: str
    size: int
    sha256: str | None


@dataclass(frozen=True)
class ModelFile:
    """One model, possibly split into several parts that llama.cpp loads together."""

    parts: tuple[FilePart, ...]

    @property
    def path(self) -> str:
        return self.parts[0].path

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def size(self) -> int:
        return sum(part.size for part in self.parts)


@dataclass(frozen=True)
class Repo:
    id: str
    sha: str
    last_modified: str | None
    gated: bool
    licence: str | None  # from the "license:" tag, e.g. "mit"; None when the repo declares none
    base_models: tuple[str, ...]
    tags: tuple[str, ...]
    downloads: int
    files: tuple[ModelFile, ...]
    mmproj: tuple[FilePart, ...]
    header: Mapping[str, Any] | None  # architecture keys of one model file; None if unreadable
    header_file: str | None = None  # the file the header was read from
    header_error: str | None = None

    @property
    def owner(self) -> str:
        return self.id.split("/", 1)[0]

    def best_mmproj(self) -> FilePart | None:
        """The vision projector to use: full precision first, then the smallest."""
        if not self.mmproj:
            return None

        def key(part: FilePart) -> tuple[int, int]:
            upper = part.path.upper()
            return (0 if ("BF16" in upper or "F16" in upper) else 1, part.size)

        return min(self.mmproj, key=key)


@dataclass(frozen=True)
class Catalog:
    fetched_at: float  # Unix time of the last full scan; 0 for an empty catalog
    repos: Mapping[str, Repo]
    source: str = "cache"  # "cache", "seed" or "empty"

    def is_stale(self, now: float, max_age: float = REFRESH_SECONDS) -> bool:
        return now - self.fetched_at > max_age


@dataclass(frozen=True)
class Entry:
    """One model file with everything the planner needs."""

    repo: Repo
    file: ModelFile
    classification: Classification
    arch: ModelArch | None

    @property
    def usable(self) -> bool:
        return self.classification.supported and self.arch is not None and not self.repo.gated


# Serialisation -----------------------------------------------------------------


def repo_to_dict(repo: Repo) -> dict[str, Any]:
    return {
        "id": repo.id,
        "sha": repo.sha,
        "last_modified": repo.last_modified,
        "gated": repo.gated,
        "licence": repo.licence,
        "base_models": list(repo.base_models),
        "tags": list(repo.tags),
        "downloads": repo.downloads,
        "files": [[_part_to_list(p) for p in f.parts] for f in repo.files],
        "mmproj": [_part_to_list(p) for p in repo.mmproj],
        "header": dict(repo.header) if repo.header is not None else None,
        "header_file": repo.header_file,
        "header_error": repo.header_error,
    }


def repo_from_dict(raw: Mapping[str, Any]) -> Repo:
    return Repo(
        id=str(raw["id"]),
        sha=str(raw["sha"]),
        last_modified=raw.get("last_modified"),
        gated=bool(raw.get("gated")),
        licence=raw.get("licence"),
        base_models=tuple(raw.get("base_models", ())),
        tags=tuple(raw.get("tags", ())),
        downloads=int(raw.get("downloads") or 0),
        files=tuple(ModelFile(tuple(_part_from_list(p) for p in f)) for f in raw["files"]),
        mmproj=tuple(_part_from_list(p) for p in raw.get("mmproj", ())),
        header=raw.get("header"),
        header_file=raw.get("header_file"),
        header_error=raw.get("header_error"),
    )


def _part_to_list(part: FilePart) -> list[Any]:
    return [part.path, part.size, part.sha256]


def _part_from_list(raw: Sequence[Any]) -> FilePart:
    return FilePart(str(raw[0]), int(raw[1]), raw[2])


def catalog_to_dict(catalog: Catalog) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "fetched_at": catalog.fetched_at,
        "repos": [repo_to_dict(r) for r in sorted(catalog.repos.values(), key=lambda r: r.id)],
    }


def catalog_from_dict(data: Mapping[str, Any], source: str) -> Catalog:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"catalog schema {data.get('schema_version')!r} is not supported")
    repos = {repo.id: repo for repo in (repo_from_dict(r) for r in data["repos"])}
    return Catalog(fetched_at=float(data["fetched_at"]), repos=repos, source=source)


# Loading -----------------------------------------------------------------------


def load_catalog(cache_file: Path) -> Catalog:
    """The cached catalog if it is usable and newer than the seed, else the shipped seed."""
    seed = _load_seed()
    try:
        raw = read_json(cache_file)
        cached = catalog_from_dict(raw, "cache") if raw is not None else None
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("ignoring unreadable catalog cache %s: %s", cache_file, exc)
        cached = None
    if cached is not None and (seed is None or cached.fetched_at >= seed.fetched_at):
        return cached
    return seed or Catalog(fetched_at=0, repos={}, source="empty")


def load_for_refresh(cache_file: Path) -> Catalog:
    """Everything already known, so a refresh re-reads only repos that changed.

    Repos from an interrupted scan (saved in the cache, possibly older than the
    seed) are merged with the seed; the cache wins where both have a repo.
    """
    seed = _load_seed()
    repos = dict(seed.repos) if seed else {}
    try:
        raw = read_json(cache_file)
        if raw is not None:
            repos.update(catalog_from_dict(raw, "cache").repos)
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("ignoring unreadable catalog cache %s: %s", cache_file, exc)
    return Catalog(fetched_at=seed.fetched_at if seed else 0, repos=repos, source="cache")


def _load_seed() -> Catalog | None:
    try:
        return catalog_from_dict(load_json(SEED_FILE), "seed")
    except DataFileError:
        return None
    except (ValueError, KeyError, TypeError) as exc:
        raise DataFileError(f"{SEED_FILE} is malformed: {exc!r}") from exc


def save_catalog(catalog: Catalog, cache_file: Path) -> None:
    atomic_write_json(cache_file, catalog_to_dict(catalog))


def entries(catalog: Catalog, families: Families, rules: Rules) -> list[Entry]:
    """Classify every model file in the catalog."""
    result = []
    for repo in catalog.repos.values():
        header = repo.header or {}
        try:
            arch = ModelArch.from_metadata(header) if header else None
        except GGUFError:
            arch = None
        for file in repo.files:
            evidence = FileEvidence(
                file_name=file.name, repo_id=repo.id, base_models=repo.base_models, tags=repo.tags
            )
            # Only the header's own file can use its general.file_type for the quant.
            metadata = header if file.path == repo.header_file else _without_file_type(header)
            classification = classify(metadata, evidence, families, rules)
            if classification.quant is None and len(repo.files) == 1:
                # One file per repo: the repo name often carries the quant the file name lacks.
                quant = quant_from_name(repo.id)
                low_bit = is_low_bit(quant, rules.low_bit_prefixes)
                classification = replace(classification, quant=quant, low_bit=low_bit)
            own_header = header if file.path == repo.header_file else None
            classification = _check_whole_model(classification, file, own_header)
            result.append(Entry(repo, file, classification, arch))
    return result


def _check_whole_model(
    classification: Classification, file: ModelFile, header: Mapping[str, Any] | None
) -> Classification:
    """Exclude files that share a model's header but are not the whole model.

    Some repos also hold per-layer files or an MTP draft head; they read like the
    full model's shape but cannot be run on their own.
    """
    shape = classification.shape
    if not classification.supported or shape is None or not shape.params_b:
        return classification
    lower = file.path.lower()
    if any(marker in lower for marker in _PARTIAL_MARKERS):
        return replace(classification, excluded="a draft or MTP head, not a whole model")
    declared = _billions(header.get("general.size_label")) if header else None
    if declared is not None and declared < shape.params_b / 2:
        return replace(
            classification,
            excluded=f"its header declares {declared:g}B parameters: a draft or partial model",
        )
    bits = file.size * 8 / (shape.params_b * 1e9)
    if bits >= MIN_BITS_PER_WEIGHT:
        return classification
    reason = (
        f"{file.size / 1024**3:.1f} GiB is too small for a whole {shape.label} model "
        f"({bits:.2f} bits per weight): probably a partial or per-layer file"
    )
    return replace(classification, excluded=reason)


def _billions(size_label: Any) -> float | None:
    """Parameters in billions from ``general.size_label``: "9B" -> 9, "256x6.7B" -> 1715.2."""
    if not isinstance(size_label, str):
        return None
    match = re.fullmatch(r"(?:(\d+)x)?(\d+(?:\.\d+)?)([BM])", size_label.strip(), re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(2)) * (int(match.group(1)) if match.group(1) else 1)
    return value / 1000 if match.group(3).upper() == "M" else value


def _without_file_type(header: Mapping[str, Any]) -> Mapping[str, Any]:
    return {k: v for k, v in header.items() if k != "general.file_type"}


# Building ----------------------------------------------------------------------


@dataclass
class ScanProgress:
    total: int = 0
    done: int = 0
    message: str = ""


def group_files(siblings: Iterable[Mapping[str, Any]]) -> tuple[list[ModelFile], list[FilePart]]:
    """Split a repo tree into model files (split parts grouped) and vision projectors."""
    singles: list[ModelFile] = []
    splits: dict[str, list[FilePart]] = {}
    mmproj: list[FilePart] = []
    for item in siblings:
        if item.get("type", "file") != "file":
            continue
        path = str(item["path"])
        name = PurePosixPath(path).name
        if not name.lower().endswith(".gguf"):
            continue
        if _not_a_model(name):
            continue
        lfs = item.get("lfs") or {}
        part = FilePart(path, int(lfs.get("size") or item.get("size") or 0), lfs.get("oid"))
        if name.lower().startswith("mmproj"):
            mmproj.append(part)
            continue
        match = _SPLIT.match(name)
        if match:
            key = str(PurePosixPath(path).parent / match["stem"]) + f"/{match['total']}"
            splits.setdefault(key, []).append(part)
        else:
            singles.append(ModelFile((part,)))
    for parts in splits.values():
        parts.sort(key=lambda p: p.path)
        singles.append(ModelFile(tuple(parts)))
    singles.sort(key=lambda f: f.path)
    return singles, mmproj


def header_subset(values: Mapping[str, Any]) -> dict[str, Any]:
    """The header keys the launcher uses: architecture numbers, the model name and file type."""
    arch = values.get("general.architecture")
    kept: dict[str, Any] = {}
    for key, value in values.items():
        ours = isinstance(arch, str) and key.startswith(f"{arch}.")
        if not (ours or key in _HEADER_GENERAL_KEYS) or isinstance(value, ArraySummary):
            continue
        if isinstance(value, str):
            if len(value) > _MAX_HEADER_STRING:
                continue
        elif isinstance(value, tuple):
            value = list(value)
        kept[key] = value
    return kept


@dataclass
class CatalogBuilder:
    hub: HubClient
    families: Families
    progress: Callable[[ScanProgress], None] | None = None
    clock: Callable[[], float] = time.time
    _progress: ScanProgress = field(default_factory=ScanProgress)

    def discover(self) -> dict[str, dict[str, Any]]:
        """Search results by repo id: the name search plus every lineage query."""
        found: dict[str, dict[str, Any]] = {}
        queries: list[list[tuple[str, str]]] = [
            [("search", name), ("filter", "gguf")] for name in self.families.search_queries
        ]
        for family in self.families.families:
            for base in family.lineage_repos:
                for relation in LINEAGE_RELATIONS:
                    queries.append([("filter", f"base_model:{relation}{base}"), ("filter", "gguf")])
        for index, query in enumerate(queries, 1):
            self._report(f"Searching Hugging Face ({index}/{len(queries)})")
            params = query + [("full", "true"), ("limit", "1000")]
            for model in self.hub.list_models(params):
                found.setdefault(model["id"], model)
        return found

    def build(
        self, previous: Catalog | None = None, save: Callable[[Catalog], None] | None = None
    ) -> Catalog:
        """Scan Hugging Face; repos unchanged since ``previous`` are reused as they are.

        ``save`` is called every few repos with the catalog so far, so an interrupted
        scan keeps its progress.
        """
        old = dict(previous.repos) if previous else {}
        found = self.discover()
        candidates = {rid: m for rid, m in found.items() if _has_model_gguf(m)}
        self._progress.total = len(candidates)
        repos: dict[str, Repo] = {}
        started = self.clock()
        for index, (repo_id, model) in enumerate(sorted(candidates.items()), 1):
            self._progress.done = index - 1
            known = old.get(repo_id)
            if known is not None and known.sha == model.get("sha") and known.header is not None:
                repos[repo_id] = known
                continue
            self._report(f"Reading {repo_id}")
            try:
                repos[repo_id] = self.read_repo(model)
            except HubError as exc:
                logger.info("skipping %s: %s", repo_id, exc)
                if known is not None:
                    repos[repo_id] = known
            if save is not None and index % 20 == 0:
                save(Catalog(fetched_at=previous.fetched_at if previous else 0, repos=repos))
        self._progress.done = self._progress.total
        self._report("Scan complete")
        return Catalog(fetched_at=started, repos=repos)

    def read_repo(self, model: Mapping[str, Any]) -> Repo:
        repo_id = str(model["id"])
        sha = str(model["sha"])
        tags = tuple(str(t) for t in model.get("tags") or ())
        gated = model.get("gated") not in (False, None)
        files: list[ModelFile] = []
        mmproj: list[FilePart] = []
        header = None
        header_file = None
        header_error = None
        if gated:
            header_error = "login required"
        else:
            files, mmproj = group_files(self.hub.tree(repo_id, sha))
            if files:
                first = min(files, key=_header_preference)
                try:
                    fetch = self.hub.range_reader(repo_id, sha, first.path)
                    metadata = gguf.read_metadata(fetch, gguf.stop_at_key_prefix("tokenizer."))
                    header = header_subset(metadata.values)
                    header_file = first.path
                except (GGUFError, NeedMoreBytes, HubError) as exc:
                    header_error = str(exc)
        return Repo(
            id=repo_id,
            sha=sha,
            last_modified=model.get("lastModified"),
            gated=gated,
            licence=_tag_value(tags, "license:"),
            base_models=_base_models(tags),
            tags=tuple(t for t in tags if not t.startswith(("base_model:", "region:"))),
            downloads=int(model.get("downloads") or 0),
            files=tuple(files),
            mmproj=tuple(mmproj),
            header=header,
            header_file=header_file,
            header_error=header_error,
        )

    def _report(self, message: str) -> None:
        self._progress.message = message
        if self.progress is not None:
            self.progress(self._progress)


def _header_preference(file: ModelFile) -> tuple[bool, bool, bool, int]:
    """Read the header of a typical file: in the repo root, with a known quant, not a head."""
    lower = file.path.lower()
    return (
        "/" in file.path and len(file.parts) == 1,
        any(marker in lower for marker in _PARTIAL_MARKERS),
        quant_from_name(file.name) is None,
        file.parts[0].size,
    )


def _has_model_gguf(model: Mapping[str, Any]) -> bool:
    for sibling in model.get("siblings") or ():
        name = PurePosixPath(str(sibling.get("rfilename", ""))).name
        if name.lower().endswith(".gguf") and not name.lower().startswith("mmproj"):
            if not _not_a_model(name):
                return True
    return False


def _not_a_model(name: str) -> bool:
    """GGUF files that hold data for quantising, not a model (importance matrices).

    Models quantised with an imatrix may say so in their names, but they carry a
    quant label; the matrix files themselves do not.
    """
    return "imatrix" in name.lower() and quant_from_name(name) is None


def _tag_value(tags: Iterable[str], prefix: str) -> str | None:
    return next((t[len(prefix) :] for t in tags if t.startswith(prefix)), None)


def _base_models(tags: Iterable[str]) -> tuple[str, ...]:
    """Repos named by ``base_model:`` tags, with any relation prefix removed."""
    bases = []
    for tag in tags:
        if not tag.startswith("base_model:"):
            continue
        value = tag[len("base_model:") :]
        for relation in LINEAGE_RELATIONS[1:]:
            if value.startswith(relation):
                value = value[len(relation) :]
        if value not in bases:
            bases.append(value)
    return tuple(bases)
