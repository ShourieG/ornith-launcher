"""Candidate settings for each model file, whether they fit this machine, and their ranking.

A candidate is one model file with a context size, KV cache type and placement.
It fits when every memory pool it uses keeps at least 15% headroom after the
OS baseline. One that fits only by eating into that headroom, or only once other
apps close, is a stretch; one that needs more than the pool has at all is too big.
The ordering and the suggested list follow the policy the owner chose (design
8.5): fit first, then how well tested, source, version, quality (quant and size),
context and KV type.
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import product
from typing import Any

from ornith_launcher.catalog.catalog import Entry
from ornith_launcher.catalog.classify import Badge, Source
from ornith_launcher.catalog.families import Families, version_key
from ornith_launcher.errors import DataFileError
from ornith_launcher.planner.memory_model import (
    GIB,
    Calibration,
    MemoryEstimate,
    estimate_memory,
)
from ornith_launcher.resources import load_json
from ornith_launcher.system.memory import Pool, PoolKind
from ornith_launcher.ui.plain import context_label

TESTED_FILE = "tested.json"

CONTEXTS = (32768, 65536, 131072, 262144, 524288, 1048576)  # 32k for low-memory machines
SHORT_CONTEXT = 65536  # below this, a context is a fallback: ranked after every longer one
KV_TYPES = ("f16", "q8_0")
DEFAULT_SLOTS = 4
HEADROOM = 0.85  # keep at least 15% of each pool free
MAX_SUGGESTED = 6
# A discrete GPU loses some memory to the driver per device when layers are split.
PER_GPU_OVERHEAD = GIB

# Quant order for "quant before context" (design 8.5 rule 5); higher is better.
_QUANT_ORDER = {"Q8_0": 6, "Q6_K": 5, "Q5_K_M": 4, "Q4_K_M": 3}
_FULL_PRECISION = ("BF16", "F16", "F32")
_QUANT_OTHER = 2
_QUANT_LOW_BIT = 1
_QUANT_FULL = 0  # listed, never suggested: Q8_0 is near-lossless at half the memory
_Q4_K_M = _QUANT_ORDER["Q4_K_M"]


class Level(str, enum.Enum):
    TESTED = "TESTED"  # measured
    INFERRED = "INFERRED"  # derived from measured runs of the same architecture
    ESTIMATED = "ESTIMATED"  # calculated only, never run


_LEVEL_ORDER = {Level.TESTED: 0, Level.INFERRED: 1, Level.ESTIMATED: 2}


class Placement(str, enum.Enum):
    SINGLE = "single"  # unified memory or CPU only: one pool
    FULL_GPU = "full-gpu"  # everything in one GPU's VRAM
    MOE_OFFLOAD = "moe-offload"  # experts in RAM (--cpu-moe), the rest in VRAM
    MULTI_GPU = "multi-gpu"  # layers split across several GPUs


class Fit(str, enum.Enum):
    """How a candidate sits in this machine's memory, for the context screen."""

    FITS = "fits"  # within the 15% headroom, and free right now
    STRETCH = "stretch"  # into the headroom, or more than is free right now
    TOO_BIG = "too big"  # more than the pool has after the OS baseline


# The Fits column wording, shared by the setup lists and ``scan --full``.
FIT_LABELS = {Fit.FITS: "yes", Fit.STRETCH: "stretch", Fit.TOO_BIG: "too big"}


THIRD_PARTY_NOTE = "third-party build, not from Ornith AI"


@dataclass(frozen=True)
class TestedResults:
    machines: Mapping[str, str]
    levels: Mapping[tuple[str, str, int, str], Level]  # (repo, file, context, kv) -> level
    notes: Mapping[tuple[str, str, int, str], str] = field(default_factory=dict)

    def level(self, repo: str, file: str, context: int, kv: str) -> Level:
        return self.levels.get((repo.lower(), file, context, kv), Level.ESTIMATED)

    def file_level(self, repo: str, file: str) -> Level:
        """The best level of any measured setting of this file."""
        found = [lvl for (r, f, _, _), lvl in self.levels.items() if (r, f) == (repo.lower(), file)]
        return min(found, key=_LEVEL_ORDER.__getitem__, default=Level.ESTIMATED)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TestedResults:
        levels = {}
        notes = {}
        try:
            for raw in data["results"]:
                key = (str(raw["repo"]).lower(), str(raw["file"]), int(raw["context"]), raw["kv"])
                levels[key] = Level(raw["level"])
                notes[key] = str(raw.get("note", ""))
            machines = dict(data["machines"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataFileError(f"{TESTED_FILE} is malformed: {exc!r}") from exc
        return cls(machines=machines, levels=levels, notes=notes)


def load_tested() -> TestedResults:
    return TestedResults.from_dict(load_json(TESTED_FILE))


@dataclass(frozen=True)
class PoolNeed:
    pool: Pool
    need: int

    @property
    def budget(self) -> int:
        return int(self.pool.usable * HEADROOM)

    @property
    def spare(self) -> int:
        return self.budget - self.need


@dataclass(frozen=True)
class Candidate:
    entry: Entry
    context: int
    kv_type: str
    slots: int
    vision: bool
    placement: Placement
    estimate: MemoryEstimate
    needs: tuple[PoolNeed, ...]
    level: Level
    file_level: Level
    yarn_factor: int | None  # set when the launcher adds YaRN flags (context above native)

    @property
    def fits(self) -> bool:
        return all(n.need <= n.budget for n in self.needs)

    @property
    def fits_now(self) -> bool:
        """Fits in the memory free right now, without closing other apps."""
        return all(
            n.pool.available_now is None or n.need <= n.pool.available_now for n in self.needs
        )

    @property
    def spare(self) -> int:
        return min(n.spare for n in self.needs)

    @property
    def fit(self) -> Fit:
        if any(n.need > n.pool.usable for n in self.needs):
            return Fit.TOO_BIG
        if not self.fits or not self.fits_now:
            return Fit.STRETCH
        return Fit.FITS

    @property
    def fit_reason(self) -> str:
        """Why a candidate is a stretch or too big; "" when it fits."""
        for n in self.needs:
            if n.need > n.pool.usable:
                return (
                    f"needs {_gib(n.need)} GiB; {n.pool.name} has {_gib(n.pool.usable)} GiB "
                    "at most after the system's own use"
                )
        for n in self.needs:
            if n.need > n.budget:
                reason = (
                    f"needs {_gib(n.need)} GiB, {_gib(n.need - n.budget)} GiB into the 15% of "
                    f"{n.pool.name} kept free: it may slow down or fail under load"
                )
                free = n.pool.available_now
                if n.pool.apps_hint and free is not None and n.need > free:
                    reason += (
                        f"; only {_gib(free)} GiB is free right now: close other apps to free "
                        f"{_gib(n.need - free)} GiB before launching"
                    )
                return reason
        for n in self.needs:
            free = n.pool.available_now
            if free is not None and n.need > free:
                return (
                    f"needs {_gib(n.need)} GiB but {_gib(free)} GiB of {n.pool.name} is free "
                    "right now: close other apps before launching"
                )
        return ""

    @property
    def shortfall(self) -> str | None:
        """Why it does not fit, e.g. "needs 74 GiB, you have 64 GiB"."""
        for n in self.needs:
            if n.need > n.budget:
                return (
                    f"needs {_gib(n.need)} GiB of {n.pool.name}, "
                    f"you have {_gib(n.budget)} GiB to spare"
                )
        return None

    @property
    def native(self) -> int:
        family = self.entry.classification.family
        return family.native_ctx if family else self.entry.arch.context_length

    @property
    def badges(self) -> tuple[str, ...]:
        badges = [self.level.value]
        badges += [b.value for b in self.entry.classification.badges]
        if self.kv_type != "f16":
            badges.append("UNTESTED KV")
        licence = self.entry.repo.licence
        if licence is None:
            badges.append("NO LICENCE")
        elif licence == "other":
            badges.append("OTHER LICENCE")
        return tuple(badges)

    @property
    def notes(self) -> tuple[str, ...]:
        notes = []
        family = self.entry.classification.family
        if self.yarn_factor and family is not None and family.yarn == "undocumented":
            notes.append(f"YaRN is not documented for Ornith {family.version}")
        if self.entry.classification.source is Source.COMMUNITY:
            notes.append(THIRD_PARTY_NOTE)
        return tuple(notes)


def build_candidates(
    entries: Iterable[Entry],
    pools: Sequence[Pool],
    calibration: Calibration,
    tested: TestedResults,
    *,
    slots: int = DEFAULT_SLOTS,
    contexts: Sequence[int] = CONTEXTS,
    kv_types: Sequence[str] = KV_TYPES,
) -> list[Candidate]:
    candidates = []
    gpu = any(p.kind in (PoolKind.VRAM, PoolKind.UNIFIED) for p in pools)
    # Measurements were taken on unified memory; other placements are estimates.
    measured_setup = any(p.kind is PoolKind.UNIFIED for p in pools)
    for entry in entries:
        if not entry.usable or entry.arch is None:
            continue
        mmproj = entry.repo.best_mmproj()
        native = entry.classification.family.native_ctx if entry.classification.family else 0
        native = native or entry.arch.context_length
        file_level = Level.ESTIMATED
        if measured_setup:
            file_level = tested.file_level(entry.repo.id, entry.file.name)
        # Vision is on by default; a row without it is offered when the model has a projector.
        visions = (True, False) if mmproj else (False,)
        for context, kv, vision in product(contexts, kv_types, visions):
            yarn = _yarn_factor(entry, context, native)
            if yarn == 0:
                continue  # not reachable with this file
            estimate = estimate_memory(
                entry.arch,
                weights=entry.file.size,
                context=context,
                calibration=calibration,
                slots=slots,
                kv_type=kv,
                mmproj=mmproj.size if vision and mmproj else 0,
                gpu=gpu,
            )
            level = Level.ESTIMATED
            if measured_setup:
                level = tested.level(entry.repo.id, entry.file.name, context, kv)
            for placement, needs in _placements(entry, estimate, pools, calibration):
                candidates.append(
                    Candidate(
                        entry=entry,
                        context=context,
                        kv_type=kv,
                        slots=slots,
                        vision=vision,
                        placement=placement,
                        estimate=estimate,
                        needs=needs,
                        level=level if placement is Placement.SINGLE else Level.ESTIMATED,
                        file_level=file_level if placement is Placement.SINGLE else Level.ESTIMATED,
                        yarn_factor=yarn,
                    )
                )
    return candidates


def _yarn_factor(entry: Entry, context: int, native: int) -> int | None:
    """None: no YaRN needed; 2 or 4: the factor; 0: this context is not offered."""
    if context <= native:
        return None
    if entry.arch.has_rope_scaling:
        # The file brings its own long-context scaling; never add flags on top.
        return None if context <= entry.arch.context_length else 0
    factor = context // native
    return factor if factor in (2, 4) and factor * native == context else 0


def _placements(
    entry: Entry, estimate: MemoryEstimate, pools: Sequence[Pool], calibration: Calibration
) -> list[tuple[Placement, tuple[PoolNeed, ...]]]:
    vram = [p for p in pools if p.kind is PoolKind.VRAM]
    ram = next((p for p in pools if p.kind is PoolKind.RAM), None)
    if not vram:
        single = next((p for p in pools if p.kind in (PoolKind.UNIFIED, PoolKind.RAM)), None)
        return [(Placement.SINGLE, (PoolNeed(single, estimate.total),))] if single else []
    if ram is None:
        return []
    # The host prompt cache lives in system RAM; everything else goes to the GPU.
    prompt_cache = estimate.overhead - calibration.overhead_base
    on_gpu = estimate.total - prompt_cache
    if len(vram) == 1:
        options = [(Placement.FULL_GPU, (PoolNeed(vram[0], on_gpu), PoolNeed(ram, prompt_cache)))]
        shape = entry.classification.shape
        if entry.arch.expert_count and shape is not None and shape.expert_share:
            weights = estimate.weights
            margin = calibration.moe_margin
            gpu_weights = int(weights * min(1.0, 1 - shape.expert_share + margin))
            ram_weights = int(weights * min(1.0, shape.expert_share + margin))
            options.append(
                (
                    Placement.MOE_OFFLOAD,
                    (
                        PoolNeed(vram[0], on_gpu - weights + gpu_weights),
                        PoolNeed(ram, prompt_cache + ram_weights),
                    ),
                )
            )
        return options
    # Several GPUs: llama.cpp splits layers across them; plan against their sum.
    combined = Pool(
        kind=PoolKind.VRAM,
        name=f"{len(vram)} GPUs",
        total=sum(p.total for p in vram) - PER_GPU_OVERHEAD * len(vram),
        baseline=sum(p.baseline for p in vram),
    )
    return [(Placement.MULTI_GPU, (PoolNeed(combined, on_gpu), PoolNeed(ram, prompt_cache)))]


# Ranking -----------------------------------------------------------------------


def quant_rank(quant: str | None, low_bit: bool) -> int:
    if quant is None:
        return _QUANT_OTHER
    base = quant.upper()
    base = base[len("UD-") :] if base.startswith("UD-") else base
    if base in _FULL_PRECISION:
        return _QUANT_FULL
    if low_bit:
        return _QUANT_LOW_BIT
    return _QUANT_ORDER.get(base, _QUANT_OTHER)


def _size_order(families: Families) -> dict[str, int]:
    shapes = sorted(families.shapes.values(), key=lambda s: s.main_layers * s.embedding_length)
    return {shape.id: index for index, shape in enumerate(shapes)}


def ranking_key(families: Families) -> Callable[[Candidate], tuple[Any, ...]]:
    sizes = _size_order(families)
    source_order = {Source.OFFICIAL: 0, Source.COMMUNITY: 1, Source.LOCAL: 3}

    def key(c: Candidate) -> tuple[Any, ...]:
        cls = c.entry.classification
        source = source_order[cls.source]
        if cls.source is Source.COMMUNITY and not cls.lineage_verified:
            source = 2
        version = tuple(-part for part in version_key(cls.version)) if cls.version else (1,)
        quant = quant_rank(cls.quant, cls.low_bit)
        shape = cls.shape
        size = sizes.get(shape.id, 0) if shape else 0
        preferred = shape is not None and shape.preferred_from_quant is not None
        bonus = size if preferred and quant >= quant_rank(shape.preferred_from_quant, False) else 0
        # Native first, then the longer YaRN contexts, then the smaller ones (largest first).
        if c.context == c.native:
            context = (0, 0)
        elif c.context > c.native:
            context = (1, c.context)
        else:
            context = (2, -c.context)
        return (
            not c.fits,
            # 32k is a fallback for small machines: any longer context that fits comes first,
            # since coding agents' own instructions fill much of a short one.
            c.context < SHORT_CONTEXT,
            _LEVEL_ORDER[c.file_level],
            source,
            cls.version is None,
            version,
            -bonus,
            -quant,
            context,
            KV_TYPES.index(c.kv_type) if c.kv_type in KV_TYPES else len(KV_TYPES),
            not c.vision,
            list(Placement).index(c.placement),
            -size,
            -c.spare,
            -c.entry.repo.downloads,
            c.entry.repo.id,
            c.entry.file.name,
        )

    return key


def rank(candidates: Iterable[Candidate], families: Families) -> list[Candidate]:
    return sorted(candidates, key=ranking_key(families))


# Filters and suggestions ----------------------------------------------------------


@dataclass(frozen=True)
class Filters:
    versions: frozenset[str] = frozenset()  # empty = all
    sizes: frozenset[str] = frozenset()  # shape labels, e.g. "35B"
    sources: frozenset[str] = frozenset()
    quants: frozenset[str] = frozenset()
    show_uncensored: bool = False
    only_uncensored: bool = False  # implies show_uncensored
    show_untested_kv: bool = False
    show_low_bit: bool = True
    only_fitting: bool = False
    search: str = ""

    def accepts(self, c: Candidate) -> bool:
        cls = c.entry.classification
        if self.versions and (cls.version or "unknown") not in self.versions:
            return False
        if self.sizes and (cls.shape.label if cls.shape else "") not in self.sizes:
            return False
        if self.sources and cls.source.value not in self.sources:
            return False
        if self.quants and (cls.quant or "").upper() not in self.quants:
            return False
        if cls.uncensored and not (self.show_uncensored or self.only_uncensored):
            return False
        if self.only_uncensored and not cls.uncensored:
            return False
        if c.kv_type != "f16" and not self.show_untested_kv:
            return False
        if cls.low_bit and not self.show_low_bit:
            return False
        if self.only_fitting and not c.fits:
            return False
        if self.search:
            text = f"{c.entry.repo.id} {c.entry.file.name}".lower()
            if not all(word in text for word in self.search.lower().split()):
                return False
        return True


@dataclass(frozen=True)
class Suggestion:
    candidate: Candidate
    reason: str


def suggest(
    ranked: Sequence[Candidate], families: Families, *, show_uncensored: bool = False
) -> list[Suggestion]:
    """At most six rows: best overall, best per size, best long context, best low-memory."""
    eligible = [c for c in ranked if _suggestible(c, show_uncensored)]
    picks: list[tuple[Candidate, str]] = []

    def add(candidate: Candidate | None, role: str) -> None:
        if candidate is None or any(_same(candidate, c) for c, _ in picks):
            return
        picks.append((candidate, role))

    if eligible:
        add(eligible[0], "Best quality that fits")
    for shape in sorted(families.shapes.values(), key=lambda s: s.main_layers * s.embedding_length):
        add(
            next((c for c in eligible if c.entry.classification.shape == shape), None),
            f"Best {shape.label}",
        )
    add(next((c for c in eligible if c.context > c.native), None), "Best long context")
    # The smallest option worth running: Q4_K_M or better when one fits.
    good = [c for c in eligible if quant_rank(c.entry.classification.quant, False) >= _Q4_K_M]
    if good or eligible:
        # min() keeps the first of equal values, so ties go to the better-ranked row.
        add(min(good or eligible, key=lambda c: c.estimate.total), "Lowest memory")
    return [Suggestion(c, _reason(c, role)) for c, role in picks[:MAX_SUGGESTED]]


def _suggestible(c: Candidate, show_uncensored: bool) -> bool:
    cls = c.entry.classification
    if not c.fits or cls.version is None or c.kv_type != "f16":
        return False
    if cls.uncensored and not show_uncensored:
        return False
    quant = quant_rank(cls.quant, cls.low_bit)
    if quant in (_QUANT_FULL, _QUANT_LOW_BIT):
        return False  # listed and choosable, never suggested
    shape = cls.shape
    if shape is not None and shape.preferred_from_quant and cls.family is not None:
        # The largest sizes are only suggested at a good quant (D29: 397B at Q4_K_M or better).
        if cls.family.untested_locally and quant < quant_rank(shape.preferred_from_quant, False):
            return False
    return True


def _same(a: Candidate, b: Candidate) -> bool:
    return (a.entry.repo.id, a.entry.file.path, a.context, a.kv_type, a.vision, a.placement) == (
        b.entry.repo.id,
        b.entry.file.path,
        b.context,
        b.kv_type,
        b.vision,
        b.placement,
    )


def _reason(c: Candidate, role: str) -> str:
    parts = [f"{role}, with {_gib(c.spare)} GiB to spare"]
    if c.context == c.native:
        parts.append("native context")
    elif c.yarn_factor:
        parts.append(f"{context_label(c.context)} context (YaRN x{c.yarn_factor}, slower prefill)")
    else:
        parts.append(f"{context_label(c.context)} context")
    if c.placement is Placement.MOE_OFFLOAD:
        parts.append("experts in system RAM")
    if not c.vision and c.entry.repo.best_mmproj() is not None:
        parts.append("vision off to save memory")
    parts.append(
        {
            Level.TESTED: "tested on DGX Spark",
            Level.INFERRED: "inferred from tests on DGX Spark",
            Level.ESTIMATED: "estimated, not run yet",
        }[c.level]
    )
    if Badge.UNTESTED_LOCALLY in c.entry.classification.badges:
        parts.append("untested locally")
    if Badge.LOW_BIT in c.entry.classification.badges:
        parts.append("low-bit: large quality loss expected")
    return "; ".join(parts) + "."


def _gib(value: int) -> str:
    return f"{value / GIB:.1f}"
