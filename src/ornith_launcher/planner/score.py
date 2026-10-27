"""A combined score for small machines: memory left, quant, size, context, popularity, trust.

"Best first" ranks by fit, how well tested, source, version and quality, in that
strict order. This score instead weighs everything at once, favouring settings
that leave memory free, for the "best for small memory" sort. Each part is 0 to 1
and the weights add up to 1. The weights and curves are a heuristic chosen by the
project, not measured: rows show the parts so the reasons stay visible.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from ornith_launcher.catalog.classify import Source
from ornith_launcher.catalog.families import version_key
from ornith_launcher.planner.rank import Candidate, Level

WEIGHTS = {
    "memory": 0.30,
    "quant": 0.25,
    "size": 0.15,
    "context": 0.10,
    "popularity": 0.10,
    "trust": 0.10,
}
# Quality by bits per weight, interpolated: little is lost above 6 bits, a lot below 4.
_QUANT_CURVE = (
    (2.0, 0.15),
    (3.0, 0.45),
    (4.0, 0.70),
    (4.5, 0.80),
    (5.0, 0.90),
    (6.0, 0.95),
    (8.0, 1.0),
)
_LARGEST_B = 397.0  # the largest Ornith size, for the size scale
# Below native; native is 1.0. 32k is low: coding agents' own instructions fill much of it.
_CONTEXT = ((32768, 0.2), (65536, 0.7), (131072, 0.85))
_BEYOND_NATIVE = 0.9  # YaRN: longer, but slower prefill on long prompts
_SOURCE = {Source.OFFICIAL: 1.0, Source.COMMUNITY: 0.7, Source.LOCAL: 0.7}
_UNVERIFIED = 0.4  # a community repo that does not declare an Ornith base model
_LEVEL = {Level.TESTED: 1.0, Level.INFERRED: 0.8, Level.ESTIMATED: 0.6}
_OLDER_VERSION = 0.6  # an older Ornith version than the newest in the list (or unknown)


@dataclass(frozen=True)
class Score:
    total: float  # 0 to 1
    parts: dict[str, float]

    def summary(self) -> str:
        """``score 72: memory 0.81, quant 0.95, ...`` for a row's note."""
        parts = ", ".join(f"{name} {value:.2f}" for name, value in self.parts.items())
        return f"score {round(self.total * 100)}: {parts} (a heuristic, not measured)"


@dataclass(frozen=True)
class Scorer:
    """Scores candidates; popularity is relative to the most downloaded and liked repo."""

    max_downloads: int = 0
    max_likes: int = 0
    newest: tuple[int, ...] | None = None  # the newest Ornith version in the list

    @classmethod
    def for_candidates(cls, candidates: Iterable[Candidate]) -> Scorer:
        candidates = list(candidates)
        repos = {c.entry.repo.id: c.entry.repo for c in candidates}.values()
        versions = [
            version_key(v) for v in {c.entry.classification.version for c in candidates} if v
        ]
        return cls(
            max_downloads=max((r.downloads for r in repos), default=0),
            max_likes=max((r.likes for r in repos), default=0),
            newest=max(versions, default=None),
        )

    def score(self, c: Candidate) -> Score:
        parts = {
            "memory": memory_part(c),
            "quant": quant_part(bits_per_weight(c)),
            "size": size_part(c),
            "context": context_part(c),
            "popularity": self.popularity_part(c),
            "trust": self.trust_part(c),
        }
        return Score(sum(WEIGHTS[name] * value for name, value in parts.items()), parts)

    def trust_part(self, c: Candidate) -> float:
        """Official over community, tested over estimated, the newest version over older ones."""
        cls = c.entry.classification
        source = _SOURCE.get(cls.source, _UNVERIFIED)
        if cls.source is Source.COMMUNITY and not cls.lineage_verified:
            source = _UNVERIFIED
        newest = cls.version is not None and version_key(cls.version) == self.newest
        version = 1.0 if newest else _OLDER_VERSION
        return 0.4 * source + 0.3 * _LEVEL[c.level] + 0.3 * version

    def popularity_part(self, c: Candidate) -> float:
        repo = c.entry.repo
        downloads = _log_share(repo.downloads, self.max_downloads)
        likes = _log_share(repo.likes, self.max_likes)
        return 0.6 * downloads + 0.4 * likes


def bits_per_weight(c: Candidate) -> float:
    """How finely the file is quantised, whatever its quant is called (NVFP4, IQ3_M, ...)."""
    shape = c.entry.classification.shape
    if shape is None or not shape.params_b:
        return 0.0
    return c.entry.file.size * 8 / (shape.params_b * 1e9)


def memory_part(c: Candidate) -> float:
    """The share of the planned limit left free (the tightest pool); 0 when over it."""
    shares = [n.spare / n.budget for n in c.needs if n.budget > 0]
    return max(0.0, min(1.0, min(shares))) if shares else 0.0


def quant_part(bits: float) -> float:
    if bits <= _QUANT_CURVE[0][0]:
        return _QUANT_CURVE[0][1] * max(0.0, bits) / _QUANT_CURVE[0][0]
    for (low, low_value), (high, high_value) in zip(_QUANT_CURVE, _QUANT_CURVE[1:]):
        if bits <= high:
            return low_value + (high_value - low_value) * (bits - low) / (high - low)
    return 1.0


def size_part(c: Candidate) -> float:
    shape = c.entry.classification.shape
    if shape is None or shape.params_b <= 1:
        return 0.0
    return min(1.0, math.log(shape.params_b) / math.log(_LARGEST_B))


def context_part(c: Candidate) -> float:
    if c.context == c.native:
        return 1.0
    if c.context > c.native:
        return _BEYOND_NATIVE
    for tokens, value in _CONTEXT:
        if c.context <= tokens:
            return value
    return _CONTEXT[-1][1]


def _log_share(value: int, largest: int) -> float:
    if largest <= 0 or value <= 0:
        return 0.0
    return math.log1p(value) / math.log1p(largest)
