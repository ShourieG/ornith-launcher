"""The setup model lists: which row each file gets, the filters and the sorts.

Suggested and the best fit list give advice: only what can run here, each file at its
recommended setting. All models is the catalog: every file at the least memory it can run
with, and fit never moves or hides a row. Filters and search only narrow a list; they never
reorder it. Everything here is pure, so the wizard's screens stay thin.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace

from ornith_launcher.catalog.catalog import Entry
from ornith_launcher.catalog.classify import Source
from ornith_launcher.catalog.families import version_key
from ornith_launcher.catalog.quant import QUANT_GROUPS, quant_group
from ornith_launcher.planner.rank import Candidate, Filters, Fit
from ornith_launcher.planner.score import Scorer, bits_per_weight

BEST_FIT, ALL_MODELS = "best fit", "all models"

FIT_ORDER = {Fit.FITS: 0, Fit.STRETCH: 1, Fit.TOO_BIG: 2}

HIDDEN, SHOWN, ONLY = "hidden", "shown", "only"
UNCENSORED_OPTIONS = (
    (HIDDEN, "Hidden"),
    (SHOWN, "Shown alongside the others"),
    (ONLY, "Only uncensored models"),
)
_UNCENSORED_SHOWING = {HIDDEN: "hidden", SHOWN: "shown", ONLY: "only uncensored"}
QUANT_LABELS = {
    "16": "16 bits (BF16, F16)",
    "8": "8 bits (Q8_0)",
    "5-6": "5 to 6 bits (Q5_K_M, Q6_K, ...)",
    "4": "4 bits (Q4_K_M, NVFP4, MXFP4, IQ4_XS, ...)",
    "low": "Below 4 bits (Q3, Q2, IQ1 to IQ3; LOW-BIT)",
}
_QUANT_SHORT = {"16": "16 bits", "8": "8 bits", "5-6": "5 to 6 bits", "4": "4 bits"}
# The finest groups down to one of these read as a floor, e.g. "4 bits or more".
_QUANT_FLOOR = {
    "16": "16 bits",
    "8": "8 bits or more",
    "5-6": "5 bits or more",
    "4": "4 bits or more",
}
SOURCE_OPTIONS = (
    (None, "Any"),
    (Source.OFFICIAL.value, "Official (Ornith AI) only"),
    (Source.COMMUNITY.value, "Community only"),
)
LICENCE_OPTIONS = ((False, "Any"), (True, 'Declares a licence (hides "none" and "other")'))
VISION_OPTIONS = ((False, "Any"), (True, "Can read images (has a vision file)"))
UPDATED_OPTIONS = (
    (None, "Any time"),
    (7, "In the last 7 days"),
    (30, "In the last 30 days"),
    (90, "In the last 90 days"),
    (365, "In the last year"),
)
LIKES_OPTIONS = ((0, "Any"), (10, "10 or more"), (100, "100 or more"), (1000, "1,000 or more"))
DOWNLOADS_OPTIONS = (
    (0, "Any"),
    (100, "100 or more"),
    (1000, "1,000 or more"),
    (10_000, "10,000 or more"),
    (100_000, "100,000 or more"),
)
LIFETIME_OPTIONS = (
    (0, "Any"),
    (1000, "1,000 or more"),
    (10_000, "10,000 or more"),
    (100_000, "100,000 or more"),
    (1_000_000, "1,000,000 or more"),
)
# The filter screen's rows, in order; each names its ListFilters field.
FILTER_FIELDS = (
    "sizes",
    "versions",
    "quants",
    "source",
    "licence",
    "vision",
    "updated_days",
    "min_likes",
    "min_downloads",
    "min_lifetime_downloads",
    "uncensored",
)


def _label(options: Sequence[tuple[object, str]], value: object) -> str:
    return next(label for option, label in options if option == value)


@dataclass(frozen=True)
class ListFilters:
    """What the two lists show; every filter looks at the file and its repo, never at fit."""

    sizes: frozenset[str] = frozenset()  # shape labels, e.g. "35B"; empty = any
    versions: frozenset[str] = frozenset()  # e.g. "1.5"; empty = any
    quants: frozenset[str] = frozenset()  # QUANT_GROUPS ids; empty = any
    source: str | None = None
    licence: bool = False  # only repos that declare a standard licence
    vision: bool = False  # only repos with a vision file
    updated_days: int | None = None
    min_likes: int = 0
    min_downloads: int = 0  # in the last 30 days, as Hugging Face counts them
    min_lifetime_downloads: int = 0
    uncensored: str = HIDDEN

    def accepts(self, entry: Entry, today: datetime.date) -> bool:
        cls = entry.classification
        repo = entry.repo
        if self.sizes and (cls.shape.label if cls.shape else "") not in self.sizes:
            return False
        if self.versions and (cls.version or "") not in self.versions:
            return False
        if self.quants and quant_group(cls.quant) not in self.quants:
            return False
        if self.source is not None and cls.source.value != self.source:
            return False
        if self.licence and repo.licence in (None, "other"):
            return False
        if self.vision and repo.best_mmproj() is None:
            return False
        if self.updated_days is not None:
            updated = updated_on(entry)
            if updated is None or (today - updated).days > self.updated_days:
                return False
        if repo.likes < self.min_likes or repo.downloads < self.min_downloads:
            return False
        if repo.downloads_all_time < self.min_lifetime_downloads:
            return False
        if cls.uncensored:
            return self.uncensored != HIDDEN
        return self.uncensored != ONLY

    @property
    def is_default(self) -> bool:
        """True when nothing is narrowed beyond the saved uncensored setting."""
        return replace(self, uncensored=HIDDEN) == ListFilters() and self.uncensored != ONLY

    def cleared(self) -> ListFilters:
        """No filters, keeping the saved uncensored setting (only is for this setup run)."""
        return ListFilters(uncensored=SHOWN if self.uncensored == SHOWN else HIDDEN)

    def showing(self) -> list[tuple[str, str]]:
        """(filter, what it shows) for each filter, in the filter screen's order."""
        return [
            ("Model size", ", ".join(sorted(self.sizes, key=_size_order)) or "any"),
            ("Version", ", ".join(sorted(self.versions, key=version_key, reverse=True)) or "any"),
            ("Quant", quant_summary(self.quants) or "any"),
            ("Source", _label(SOURCE_OPTIONS, self.source).lower()),
            ("Licence", "declares a licence" if self.licence else "any"),
            ("Vision", "can read images" if self.vision else "any"),
            ("Updated", _label(UPDATED_OPTIONS, self.updated_days).lower()),
            ("Likes", _label(LIKES_OPTIONS, self.min_likes).lower()),
            ("Downloads (30 days)", _label(DOWNLOADS_OPTIONS, self.min_downloads).lower()),
            ("Downloads (lifetime)", _label(LIFETIME_OPTIONS, self.min_lifetime_downloads).lower()),
            ("Uncensored", _UNCENSORED_SHOWING[self.uncensored]),
        ]

    def summary(self) -> str:
        """The active filters on one line for the list screens, or "none"."""
        parts = []
        if self.sizes:
            parts.append("size " + " or ".join(sorted(self.sizes, key=_size_order)))
        if self.versions:
            versions = sorted(self.versions, key=version_key, reverse=True)
            parts.append("version " + " or ".join(versions))
        if self.quants:
            parts.append(quant_summary(self.quants))
        if self.source is not None:
            parts.append(f"{self.source} only")
        if self.licence:
            parts.append("declares a licence")
        if self.vision:
            parts.append("can read images")
        if self.updated_days is not None:
            parts.append("updated " + _label(UPDATED_OPTIONS, self.updated_days).lower())
        if self.min_likes:
            parts.append(f"{_label(LIKES_OPTIONS, self.min_likes)} likes")
        if self.min_downloads:
            parts.append(f"{_label(DOWNLOADS_OPTIONS, self.min_downloads)} downloads in 30 days")
        if self.min_lifetime_downloads:
            lifetime = _label(LIFETIME_OPTIONS, self.min_lifetime_downloads)
            parts.append(f"{lifetime} downloads in all")
        if self.uncensored == SHOWN:
            parts.append("uncensored shown")
        elif self.uncensored == ONLY:
            parts.append("only uncensored")
        return "; ".join(parts) or "none"


def quant_summary(groups: frozenset[str]) -> str:
    """ "4 bits or more" for the finest groups down to 4 bits, else the groups by name."""
    if not groups or groups == frozenset(QUANT_GROUPS):
        return ""
    ordered = [g for g in QUANT_GROUPS if g in groups]
    if ordered == list(QUANT_GROUPS[: len(ordered)]):
        return _QUANT_FLOOR[ordered[-1]]
    return ", ".join(_QUANT_SHORT.get(g, "below 4 bits") for g in ordered)


def _size_order(label: str) -> float:
    try:
        return float(label.rstrip("B"))
    except ValueError:
        return float("inf")


def updated_on(entry: Entry) -> datetime.date | None:
    """When the file's Hugging Face repo last changed, or None when the catalog does not say."""
    stamp = entry.repo.last_modified
    if not stamp:
        return None
    try:
        return datetime.date.fromisoformat(stamp[:10])
    except ValueError:
        return None


# Rows: one candidate per file ---------------------------------------------------------------

FileKey = tuple[str, str]


def file_key(c: Candidate) -> FileKey:
    return (c.entry.repo.id, c.entry.file.path)


def _listed(ranked: Iterable[Candidate], search: str) -> Iterable[tuple[int, Candidate]]:
    # Uncensored files pass here; ListFilters decides on them with the rest.
    base = Filters(show_uncensored=True, search=search)
    return ((i, c) for i, c in enumerate(ranked) if base.accepts(c))


def _pick(
    ranked: Sequence[Candidate], search: str, score: Callable[[int, Candidate], tuple]
) -> dict[FileKey, Candidate]:
    """Each file's candidate with the lowest ``score``, in the order of those scores."""
    best: dict[FileKey, tuple[tuple, Candidate]] = {}
    for position, c in _listed(ranked, search):
        key = file_key(c)
        value = score(position, c)
        if key not in best or value < best[key][0]:
            best[key] = (value, c)
    return {key: c for key, (_, c) in sorted(best.items(), key=lambda item: item[1][0])}


def recommended(ranked: Sequence[Candidate], search: str = "") -> dict[FileKey, Candidate]:
    """Each file at its recommended setting, best first; fitting files come first.

    A file with no setting that fits gets its smallest one (vision on where it has a
    projector), so its verdict (stretch or too big) is the best case. This is the setting
    the context screen starts from, whichever list the file was picked in.
    """

    def score(position: int, c: Candidate) -> tuple:
        order = FIT_ORDER[c.fit]
        # Vision stays as the ranking has it (on first), so the next screens match the row.
        return (
            order,
            0 if order == 0 or c.vision else 1,
            position if order == 0 else c.estimate.total,
        )

    return _pick(ranked, search, score)


def lightest(ranked: Sequence[Candidate], search: str = "") -> dict[FileKey, Candidate]:
    """Each file at the least memory it can run with: the shortest context, vision off."""
    return _pick(ranked, search, lambda position, c: (c.estimate.total, position))


def highest_scoring(
    ranked: Sequence[Candidate], scorer: Scorer, search: str = ""
) -> dict[FileKey, Candidate]:
    """Each file at its highest combined score among the settings that can run here."""
    return _pick(
        ranked, search, lambda position, c: (FIT_ORDER[c.fit], -scorer.score(c).total, position)
    )


@dataclass(frozen=True)
class Lists:
    """The two lists' rows before sorting, plus what the screens say about them."""

    best_fit: list[Candidate]  # files that can run here (yes or stretch)
    all_models: list[Candidate]  # every file, at its lightest setting
    recommended: dict[FileKey, Candidate] = field(repr=False)  # what a pick starts from
    too_big: int  # files that match but are too big for the best fit list
    total: int  # files in the catalog, with only the uncensored setting applied


def build_lists(
    ranked: Sequence[Candidate],
    filters: ListFilters,
    search: str,
    today: datetime.date,
    scorer: Scorer | None = None,
) -> Lists:
    """Rows for both lists; with ``scorer``, best fit shows each file's highest score."""

    def keep(c: Candidate) -> bool:
        return filters.accepts(c.entry, today)

    picks = recommended(ranked, search)
    fit_rows = highest_scoring(ranked, scorer, search) if scorer is not None else picks
    best_fit = [c for c in fit_rows.values() if c.fit is not Fit.TOO_BIG and keep(c)]
    all_models = [c for c in lightest(ranked, search).values() if keep(c)]
    everything = lightest(ranked).values()
    visible = ListFilters(uncensored=filters.uncensored)
    total = sum(1 for c in everything if visible.accepts(c.entry, today))
    return Lists(best_fit, all_models, picks, len(all_models) - len(best_fit), total)


def count_matching(
    rows: Iterable[Candidate], filters: ListFilters, today: datetime.date
) -> tuple[int, int]:
    """(files that pass ``filters``, how many of them can run here), for the filter screen.

    ``rows`` are the lightest rows of every file, before filters.
    """
    matching = [c for c in rows if filters.accepts(c.entry, today)]
    return len(matching), sum(1 for c in matching if c.fit is not Fit.TOO_BIG)


# Sorts --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Sort:
    label: str
    by: str
    largest_first: bool = False
    advice: bool = False  # ranks by how well a setting suits this machine


_PLAIN = (
    Sort("memory, smallest first", "memory"),
    Sort("memory, largest first", "memory", True),
    Sort("download size, smallest first", "download"),
    Sort("download size, largest first", "download", True),
    Sort("quant, smallest first (bits per weight)", "quant"),
    Sort("quant, largest first (bits per weight)", "quant", True),
    Sort("likes, most first", "likes", True),
    Sort("downloads (30 days), most first", "downloads", True),
    Sort("downloads (lifetime), most first", "lifetime", True),
    Sort("updated, newest first", "updated", True),
)
BEST_FIT_SORTS = (
    Sort("best first", "best", advice=True),
    Sort("best for small memory, a combined score", "score", True, advice=True),
    *_PLAIN,
)
ALL_MODELS_SORTS = (
    Sort("by model, newest and largest first", "model"),
    Sort("model size, smallest first", "size"),
    Sort("memory needed, smallest first", "memory"),
    Sort("memory needed, largest first", "memory", True),
    *_PLAIN[2:],
)
SORTS = {BEST_FIT: BEST_FIT_SORTS, ALL_MODELS: ALL_MODELS_SORTS}


def download_size(entry: Entry, vision: bool) -> int:
    """Bytes to download: the model file, plus the vision file when vision is on."""
    mmproj = entry.repo.best_mmproj()
    return entry.file.size + (mmproj.size if vision and mmproj is not None else 0)


def _model_order(c: Candidate) -> tuple:
    """Newest version, then the largest model, then the finest quant; official first."""
    cls = c.entry.classification
    version = tuple(-part for part in version_key(cls.version)) if cls.version else (1,)
    params = cls.shape.params_b if cls.shape else 0.0
    official = cls.source is not Source.OFFICIAL
    return (cls.version is None, version, -params, -bits_per_weight(c), official)


def _size_first(c: Candidate) -> tuple:
    cls = c.entry.classification
    return (cls.shape.params_b if cls.shape else float("inf"), *_model_order(c)[:2])


def _updated(c: Candidate) -> int:
    day = updated_on(c.entry)
    return day.toordinal() if day else 0


_VALUES: dict[str, Callable[[Candidate], float]] = {
    "memory": lambda c: c.estimate.total,
    "download": lambda c: download_size(c.entry, c.vision),
    "quant": bits_per_weight,
    "likes": lambda c: c.entry.repo.likes,
    "downloads": lambda c: c.entry.repo.downloads,
    "lifetime": lambda c: c.entry.repo.downloads_all_time,
    "updated": _updated,
}


def sort_rows(
    rows: Sequence[Candidate], sort: Sort, scorer: Scorer | None = None
) -> list[Candidate]:
    """``rows`` in ``sort``'s order; ties keep the order they came in (best first)."""
    if sort.by == "best":
        return list(rows)  # recommended() already gives them best first
    if sort.by == "score":
        scorer = scorer or Scorer.for_candidates(rows)
        return sorted(rows, key=lambda c: (FIT_ORDER[c.fit], -scorer.score(c).total))
    if sort.by == "model":
        return sorted(rows, key=_model_order)
    if sort.by == "size":
        return sorted(rows, key=_size_first)
    value = _VALUES[sort.by]
    sign = -1 if sort.largest_first else 1
    return sorted(rows, key=lambda c: sign * value(c))
