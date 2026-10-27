"""Models already on the machine: a quick search of the usual folders, and paths the user enters.

The search never covers the whole disk and never changes what it finds: a
chosen model is used where it is, read-only. Known layouts (the Hugging Face
cache, Ollama's blob store) are read by their structure; other folders are
walked a few levels deep with a limit on the number of entries.
"""

from __future__ import annotations

import os
import re
import stat
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ornith_launcher.catalog import gguf
from ornith_launcher.catalog.arch import ModelArch
from ornith_launcher.catalog.catalog import (
    Entry,
    FilePart,
    ModelFile,
    Repo,
    header_subset,
    is_projector,
)
from ornith_launcher.catalog.classify import Classification, FileEvidence, Rules, Source, classify
from ornith_launcher.catalog.families import Families
from ornith_launcher.catalog.gguf import GGUFError, NeedMoreBytes
from ornith_launcher.catalog.quant import quant_from_file_type, quant_from_name
from ornith_launcher.config import atomic_write_json, read_json
from ornith_launcher.download import sha256_file

MIN_MODEL_SIZE = 1024**3
MAX_DEPTH = 3
MAX_ENTRIES = 20_000
FOLDER_TIME_LIMIT = 3.0  # seconds; a slower folder (e.g. a network share) is skipped
SKIP_DIRS = frozenset({"node_modules", ".git", "__pycache__"})
SKIP_SUFFIXES = (".part", ".incomplete", ".part.json", ".tmp")

# Windows: cloud files that are not on disk yet. Reading them would start a download.
_RECALL_ON_DATA_ACCESS = 0x400000
_OFFLINE = 0x1000

_SPLIT = re.compile(r"^(?P<stem>.+)-(?P<part>\d{5})-of-(?P<total>\d{5})\.gguf$", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Location:
    path: Path
    owner_app: str | None  # "hf-cache", "lmstudio", "ollama", "llamacpp-cache" or None
    layout: str  # "hf-cache", "ollama" or "tree"
    label: str


@dataclass(frozen=True)
class LocalFile:
    path: Path  # as found (may be a symlink, e.g. in the Hugging Face cache)
    real_path: Path
    size: int  # all parts together
    mtime_ns: int
    owner_app: str | None
    parts: tuple[Path, ...] = ()  # real paths of every split part, first part first
    sha256_hint: str | None = None  # known without hashing: HF blob or Ollama blob name
    repo_hint: str | None = None  # "org/repo" when the folder layout names one
    incomplete: str | None = None  # e.g. "parts 2-3 missing"
    online_only: bool = False  # Windows cloud placeholder: never opened


@dataclass(frozen=True)
class LocalModel:
    file: LocalFile
    header: Mapping[str, Any]
    arch: ModelArch | None
    classification: Classification
    likely: Entry | None  # a catalog file with the same name or checksum and size
    confirmed: bool  # the sha256 is known to match ``likely``
    mmproj: Path | None = None

    @property
    def supported(self) -> bool:
        return self.classification.supported and self.file.incomplete is None


@dataclass
class SearchResult:
    files: list[LocalFile] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)  # (folder, why)


def search_locations(
    env: Mapping[str, str],
    home: Path,
    os_name: str,
    models_dir: Path,
    extra_dirs: Sequence[Path] = (),
) -> list[Location]:
    """Where the quick search looks, per OS (design 7.7); only existing folders are searched."""
    locations: list[Location] = []

    def add(path: Path | str | None, owner: str | None, layout: str, label: str) -> None:
        if not path:
            return
        path = Path(path).expanduser()
        if all(loc.path != path for loc in locations):
            locations.append(Location(path, owner, layout, label))

    hub = env.get("HF_HUB_CACHE") or (
        str(Path(env["HF_HOME"]) / "hub") if env.get("HF_HOME") else None
    )
    add(
        hub or home / ".cache" / "huggingface" / "hub", "hf-cache", "hf-cache", "Hugging Face cache"
    )
    add(home / ".lmstudio" / "models", "lmstudio", "tree", "LM Studio")
    if os_name == "linux":
        add(home / ".cache" / "lm-studio" / "models", "lmstudio", "tree", "LM Studio (old)")
    ollama = env.get("OLLAMA_MODELS")
    add(ollama or home / ".ollama" / "models", "ollama", "ollama", "Ollama")
    if os_name == "linux" and not ollama:
        add("/usr/share/ollama/.ollama/models", "ollama", "ollama", "Ollama (service)")
    add(env.get("LLAMA_CACHE"), "llamacpp-cache", "tree", "llama.cpp cache")
    if os_name == "linux":
        add(home / ".cache" / "llama.cpp", "llamacpp-cache", "tree", "llama.cpp cache (old)")
    elif os_name == "darwin":
        cache = home / "Library" / "Caches" / "llama.cpp"
        add(cache, "llamacpp-cache", "tree", "llama.cpp cache (old)")
    add(home / "models", None, "tree", "~/models")
    if os_name != "darwin":
        # macOS asks the user before an app reads ~/Downloads; the automatic search skips it.
        add(home / "Downloads", None, "tree", "Downloads")
    add(models_dir, None, "tree", "models folder")
    for folder in extra_dirs:
        add(folder, None, "tree", str(folder))
    return locations


def quick_search(
    locations: Iterable[Location], clock: Callable[[], float] = time.monotonic
) -> SearchResult:
    result = SearchResult()
    seen: set[Path] = set()
    for location in locations:
        if not location.path.is_dir():
            continue
        try:
            if location.layout == "hf-cache":
                found = list(_hf_cache_files(location))
            elif location.layout == "ollama":
                found = list(_ollama_files(location))
            else:
                found = _walk(location, location.path, clock)
        except _TooSlow:
            result.skipped.append((location.path, "too slow to search (network share?)"))
            continue
        except _TooBig:
            result.skipped.append((location.path, f"more than {MAX_ENTRIES} entries"))
            continue
        for item in found:
            if item.real_path not in seen:
                seen.add(item.real_path)
                result.files.append(item)
    return result


class _TooSlow(Exception):
    pass


class _TooBig(Exception):
    pass


def _hf_cache_files(location: Location) -> Iterator[LocalFile]:
    # models--<org>--<repo>/snapshots/<revision>/<path> -> ../../blobs/<sha256>
    for repo_dir in sorted(location.path.glob("models--*")):
        repo_id = repo_dir.name[len("models--") :].replace("--", "/", 1)
        snapshots = repo_dir / "snapshots"
        if not snapshots.is_dir():
            continue
        for path in sorted(snapshots.rglob("*")):
            if path.suffix.lower() != ".gguf" or is_projector(path.name):
                continue
            item = _local_file(path, location.owner_app, repo_hint=repo_id)
            if item is not None:
                blob = item.real_path.name
                yield _with_hint(item, blob if _SHA256.match(blob) else None)


def _ollama_files(location: Location) -> Iterator[LocalFile]:
    blobs = location.path / "blobs"
    if not blobs.is_dir():
        return
    for path in sorted(blobs.iterdir()):
        if not path.name.startswith("sha256-"):
            continue
        item = _local_file(path, location.owner_app, require_extension=False)
        if item is not None:
            yield _with_hint(item, path.name[len("sha256-") :].lower())


def _walk(location: Location, root: Path, clock: Callable[[], float]) -> list[LocalFile]:
    started = clock()
    entries = 0
    found: list[LocalFile] = []
    by_folder: dict[Path, list[str]] = {}

    def visit(folder: Path, depth: int) -> None:
        nonlocal entries
        try:
            children = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            return
        names = []
        for child in children:
            entries += 1
            if entries > MAX_ENTRIES:
                raise _TooBig()
            if clock() - started > FOLDER_TIME_LIMIT:
                raise _TooSlow()
            if child.name.startswith(".") or child.name in SKIP_DIRS:
                continue
            try:
                if child.is_dir():
                    if depth < MAX_DEPTH:
                        visit(Path(child.path), depth + 1)
                    continue
            except OSError:
                continue
            names.append(child.name)
        by_folder[folder] = names

    visit(root, 1)
    for folder, names in by_folder.items():
        lower = {name.lower() for name in names}
        for name in names:
            low = name.lower()
            if not low.endswith(".gguf") or is_projector(name) or low.endswith(SKIP_SUFFIXES):
                continue
            split = _SPLIT.match(name)
            if split and int(split["part"]) != 1:
                continue  # only the first part holds the header
            repo_hint = None
            if location.owner_app == "lmstudio" and folder.parent.parent == root:
                repo_hint = f"{folder.parent.name}/{folder.name}"
            item = _local_file(folder / name, location.owner_app, repo_hint=repo_hint)
            if item is None:
                continue
            if split:
                item = _with_split_parts(item, folder, split, lower)
            found.append(item)
    return found


def _local_file(
    path: Path,
    owner_app: str | None,
    *,
    require_extension: bool = True,
    repo_hint: str | None = None,
) -> LocalFile | None:
    """A LocalFile if ``path`` looks like a model (big enough, GGUF magic), else None."""
    if require_extension and path.suffix.lower() != ".gguf":
        return None
    try:
        info = path.stat()  # follows symlinks
        real = path.resolve()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    attributes = getattr(info, "st_file_attributes", 0)
    online_only = bool(attributes & (_RECALL_ON_DATA_ACCESS | _OFFLINE))
    split = _SPLIT.match(path.name)
    if info.st_size < MIN_MODEL_SIZE and not split:
        return None
    if not online_only:
        try:
            with open(real, "rb") as handle:
                if handle.read(4) != gguf.MAGIC:
                    return None
        except OSError:
            return None
    return LocalFile(
        path=path,
        real_path=real,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        owner_app=owner_app,
        parts=(real,),
        repo_hint=repo_hint,
        online_only=online_only,
    )


def _with_hint(item: LocalFile, sha256: str | None) -> LocalFile:
    return LocalFile(**{**item.__dict__, "sha256_hint": sha256})


def _with_split_parts(item: LocalFile, folder: Path, split: re.Match, names: set[str]) -> LocalFile:
    total = int(split["total"])
    parts = []
    missing = []
    for number in range(1, total + 1):
        name = f"{split['stem']}-{number:05d}-of-{split['total']}.gguf"
        if name.lower() in names:
            parts.append((folder / name).resolve())
        else:
            missing.append(number)
    size = sum(p.stat().st_size for p in parts)
    incomplete = None
    if missing:
        incomplete = f"incomplete: part{'s' if len(missing) > 1 else ''} {_ranges(missing)} missing"
    return LocalFile(
        **{**item.__dict__, "size": size, "parts": tuple(parts), "incomplete": incomplete}
    )


def _ranges(numbers: list[int]) -> str:
    if len(numbers) > 1 and numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"{numbers[0]}-{numbers[-1]}"
    return ", ".join(str(n) for n in numbers)


# Identification -----------------------------------------------------------------


def identify(
    item: LocalFile, families: Families, rules: Rules, catalog: Sequence[Entry] = ()
) -> LocalModel | None:
    """Read the header and classify the file; None if it cannot be read as a GGUF model."""
    if item.online_only:
        return None
    try:
        fetch = gguf.file_prefix_reader(item.real_path)
        metadata = gguf.read_metadata(fetch, gguf.stop_at_key_prefix("tokenizer."))
        values = dict(metadata.values)
        if quant_from_name(item.path.name) is None and "general.file_type" not in values:
            # general.file_type is written after the tokenizer: read the whole header.
            values = dict(gguf.read_metadata(fetch).values)
    except (OSError, GGUFError, NeedMoreBytes):
        return None
    header = header_subset(values)
    try:
        arch = ModelArch.from_metadata(header)
    except GGUFError:
        arch = None
    likely, confirmed = _match(item, catalog)
    evidence = FileEvidence(
        file_name=item.path.name if item.owner_app != "ollama" else _header_file_name(header),
        repo_id=likely.repo.id if likely else None,
        base_models=likely.repo.base_models if likely else (),
        tags=likely.repo.tags if likely else (),
    )
    classification = classify(header, evidence, families, rules)
    if classification.quant is None:
        classification = _with_quant(
            classification, quant_from_file_type(header.get("general.file_type"))
        )
    return LocalModel(
        file=item,
        header=header,
        arch=arch,
        classification=classification,
        likely=likely,
        confirmed=confirmed,
        mmproj=_find_mmproj(item, header),
    )


def _header_file_name(header: Mapping[str, Any]) -> str:
    name = header.get("general.name")
    return f"{name}.gguf" if isinstance(name, str) else "model.gguf"


def _with_quant(classification: Classification, quant: str | None) -> Classification:
    if quant is None:
        return classification
    return Classification(**{**classification.__dict__, "quant": quant})


def _match(item: LocalFile, catalog: Sequence[Entry]) -> tuple[Entry | None, bool]:
    """A catalog file this one probably is: same checksum, else same name and size.

    Popular files are re-uploaded by others under the same name, so among equal
    matches the official repo wins, then repos that declare an Ornith base model.
    """
    if item.sha256_hint:
        found = [
            e
            for e in catalog
            if len(e.file.parts) == 1 and e.file.parts[0].sha256 == item.sha256_hint
        ]
        if found:
            return _best_repo(found), True
    hint = item.repo_hint.lower() if item.repo_hint else None
    found = [
        e
        for e in catalog
        if e.file.size == item.size
        and e.file.name == item.path.name
        and (hint is None or e.repo.id.lower() == hint)
    ]
    if not found and hint:
        found = [e for e in catalog if e.repo.id.lower() == hint and e.file.size == item.size]
    return (_best_repo(found), False) if found else (None, False)


def _best_repo(entries: Sequence[Entry]) -> Entry:
    order = {Source.OFFICIAL: 0, Source.COMMUNITY: 1, Source.LOCAL: 2}
    return min(
        entries,
        key=lambda e: (
            order[e.classification.source],
            not e.classification.lineage_verified,
            -e.repo.downloads,
            e.repo.id,
        ),
    )


def sort_found(models: Sequence[LocalModel]) -> list[LocalModel]:
    """Usable models first, uncensored ones last, catalog matches before unknown files."""
    return sorted(
        models,
        key=lambda m: (
            not m.supported,
            m.classification.uncensored,
            m.likely is None,
            str(m.file.path),
        ),
    )


def _find_mmproj(item: LocalFile, header: Mapping[str, Any]) -> Path | None:
    """A vision projector next to the model, if there is one."""
    if item.owner_app == "ollama":
        return None
    folder = item.path.parent
    try:
        candidates = sorted(p for p in folder.iterdir() if is_projector(p.name))
    except OSError:
        return None
    candidates = [p for p in candidates if p.suffix.lower() == ".gguf"]
    if not candidates:
        return None
    full = [p for p in candidates if "16" in p.name]  # BF16 / F16 first
    return (full or candidates)[0].resolve()


def as_entry(model: LocalModel) -> Entry:
    """A catalog entry for planning: the local file with its catalog match's repo data, if any."""
    item = model.file
    sizes = [p.stat().st_size for p in item.parts] if len(item.parts) > 1 else [item.size]
    parts = tuple(
        FilePart(str(path), size, item.sha256_hint if len(item.parts) == 1 else None)
        for path, size in zip(item.parts, sizes)
    )
    file = ModelFile(parts)
    mmproj: tuple[FilePart, ...] = ()
    if model.mmproj is not None:
        mmproj = (FilePart(str(model.mmproj), model.mmproj.stat().st_size, None),)
    likely = model.likely
    repo = Repo(
        id=likely.repo.id if likely else f"local/{item.path.name}",
        sha=likely.repo.sha if likely else "",
        last_modified=None,
        gated=False,
        licence=likely.repo.licence if likely else None,
        base_models=likely.repo.base_models if likely else (),
        tags=likely.repo.tags if likely else (),
        downloads=0,
        files=(file,),
        mmproj=mmproj,
        header=model.header,
        header_file=file.path,
    )
    return Entry(repo, file, model.classification, model.arch)


def confirm_checksum(
    model: LocalModel, cache_file: Path, on_progress: Callable[[int], None] | None = None
) -> bool:
    """Hash the file and compare it with its catalog match."""
    if model.confirmed:
        return True
    likely = model.likely
    if likely is None or len(likely.file.parts) != 1 or not likely.file.parts[0].sha256:
        return False
    digest = cached_sha256(model.file.real_path, cache_file, on_progress)
    return digest == likely.file.parts[0].sha256


def cached_sha256(
    path: Path, cache_file: Path, on_progress: Callable[[int], None] | None = None
) -> str:
    """The file's sha256, reusing an earlier result while the file is unchanged.

    Hashing a 35 GiB file takes about a minute, so digests are stored by
    (real path, size, mtime) in ``cache_file``.
    """
    info = path.stat()
    key = f"{path.resolve()}|{info.st_size}|{info.st_mtime_ns}"
    try:
        cache = read_json(cache_file) or {}
    except ValueError:
        cache = {}
    digest = cache.get(key)
    if digest is None:
        digest = sha256_file(path, on_progress)
        cache[key] = digest
        atomic_write_json(cache_file, cache)
    return digest


# Paths typed or dragged in by the user ------------------------------------------------

_ENV_VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)|%(\w+)%")


def clean_path_input(
    text: str, env: Mapping[str, str], home: Path, posix: bool = os.sep == "/"
) -> Path:
    """Turn what the user typed or dragged into the terminal into a path.

    Terminals add quotes or backslash escapes around dragged files, and users type
    ``~`` and environment variables. On Windows a backslash separates folders, so
    only an escaped space is undone there.
    """
    text = text.strip()
    if text.startswith("file://"):
        text = text[len("file://") :]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    text = re.sub(r"\\(.)", r"\1", text) if posix else text.replace("\\ ", " ")

    def var(match: re.Match) -> str:
        name = match.group(1) or match.group(2) or match.group(3)
        return env.get(name, match.group(0))

    text = _ENV_VAR.sub(var, text)
    if text == "~" or text.startswith(("~/", "~\\")):
        text = str(home) + text[1:]
    return Path(text)


def check_entered_path(path: Path) -> str | None:
    """Why ``path`` cannot be used, or None if it can be searched."""
    if not path.exists():
        return f"{path} does not exist. Check the spelling, or drag the file into the terminal."
    if not os.access(path, os.R_OK):
        return f"{path} cannot be read by this user. Check its permissions (ls -l)."
    return None


def search_entered_path(path: Path, clock: Callable[[], float] = time.monotonic) -> SearchResult:
    """Search a file or folder the user entered, with the quick-search limits."""
    result = SearchResult()
    if path.is_file():
        item = _local_file(path, None, require_extension=False)
        if item is not None:
            split = _SPLIT.match(path.name)
            if split:
                names = {p.name.lower() for p in path.parent.iterdir()}
                item = _with_split_parts(item, path.parent, split, names)
            result.files.append(item)
        return result
    location = Location(path, None, "tree", str(path))
    try:
        result.files.extend(_walk(location, path, clock))
    except (_TooSlow, _TooBig) as exc:
        result.skipped.append(
            (path, "too slow" if isinstance(exc, _TooSlow) else "too many entries")
        )
    return result
