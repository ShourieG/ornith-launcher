from __future__ import annotations

import datetime
import unittest
from dataclasses import replace
from types import SimpleNamespace

from ornith_launcher.browse import (
    ALL_MODELS_SORTS,
    BEST_FIT_SORTS,
    HIDDEN,
    ONLY,
    SHOWN,
    ListFilters,
    build_lists,
    count_matching,
    lightest,
    quant_summary,
    recommended,
    sort_rows,
    updated_on,
)
from ornith_launcher.catalog.catalog import entries
from ornith_launcher.catalog.classify import Source, load_rules
from ornith_launcher.catalog.families import load_families
from ornith_launcher.catalog.quant import quant_group
from ornith_launcher.planner.memory_model import load_calibration
from ornith_launcher.planner.rank import Fit, build_candidates, load_tested, rank
from tests.catalog_fixtures import official_catalog
from tests.test_rank import MACHINES

GIB = 1024**3
TODAY = datetime.date(2026, 10, 27)


def entry(
    *,
    size="35B",
    params=35.0,
    version="1.5",
    quant="Q8_0",
    source=Source.OFFICIAL,
    uncensored=False,
    licence="mit",
    vision=False,
    updated="2026-10-20T08:00:00.000Z",
    likes=0,
    downloads=0,
    lifetime=0,
    file_gib=36.0,
    repo="quant-uploader/Ornith-GGUF",
    name="Ornith-Q8_0.gguf",
):
    shape = SimpleNamespace(label=size, params_b=params)
    cls = SimpleNamespace(
        shape=shape, version=version, quant=quant, source=source, uncensored=uncensored
    )
    projector = object() if vision else None
    repo = SimpleNamespace(
        id=repo,
        licence=licence,
        best_mmproj=lambda: projector,
        last_modified=updated,
        likes=likes,
        downloads=downloads,
        downloads_all_time=lifetime,
    )
    file = SimpleNamespace(size=int(file_gib * GIB), path=name, name=name)
    return SimpleNamespace(classification=cls, repo=repo, file=file)


def row(name, fit=Fit.FITS, memory=40, **fields):
    e = entry(name=name, **fields)
    return SimpleNamespace(
        name=name,
        entry=e,
        fit=fit,
        fits=fit is Fit.FITS,
        vision=False,
        estimate=SimpleNamespace(total=memory * GIB),
    )


class QuantGroupTest(unittest.TestCase):
    def test_groups_by_bits_whatever_the_quant_is_called(self):
        cases = {
            "BF16": "16",
            "F16": "16",
            "Q8_0": "8",
            "UD-Q8_K_XL": "8",
            "Q6_K": "5-6",
            "Q5_K_M": "5-6",
            "Q4_K_M": "4",
            "IQ4_XS": "4",
            "NVFP4": "4",
            "MXFP4_MOE": "4",
            "Q3_K_M": "low",
            "UD-IQ1_S": "low",
            "TQ1_0": "low",
            None: None,
            "WEIRD": None,
        }
        for quant, group in cases.items():
            with self.subTest(quant):
                self.assertEqual(quant_group(quant), group)

    def test_summary_names_a_floor_or_the_groups(self):
        self.assertEqual(quant_summary(frozenset()), "")
        self.assertEqual(quant_summary(frozenset({"16", "8", "5-6", "4"})), "4 bits or more")
        self.assertEqual(quant_summary(frozenset({"16", "8"})), "8 bits or more")
        self.assertEqual(quant_summary(frozenset({"8", "4"})), "8 bits, 4 bits")
        self.assertEqual(quant_summary(frozenset({"low"})), "below 4 bits")
        everything = frozenset({"16", "8", "5-6", "4", "low"})
        self.assertEqual(quant_summary(everything), "")  # all ticked is the same as any


class ListFiltersTest(unittest.TestCase):
    def accepts(self, filters, **fields):
        return filters.accepts(entry(**fields), TODAY)

    def test_each_filter_looks_at_the_file_and_its_repo(self):
        cases = [
            (ListFilters(sizes=frozenset({"9B", "122B"})), {"size": "9B"}, {"size": "35B"}),
            (ListFilters(versions=frozenset({"1.0"})), {"version": "1.0"}, {"version": None}),
            (ListFilters(quants=frozenset({"4"})), {"quant": "NVFP4"}, {"quant": "Q8_0"}),
            (
                ListFilters(source="community"),
                {"source": Source.COMMUNITY},
                {"source": Source.OFFICIAL},
            ),
            (ListFilters(licence=True), {"licence": "apache-2.0"}, {"licence": "other"}),
            (ListFilters(vision=True), {"vision": True}, {"vision": False}),
            (
                ListFilters(updated_days=30),
                {"updated": "2026-10-01T00:00:00Z"},
                {"updated": "2026-08-01T00:00:00Z"},
            ),
            (ListFilters(updated_days=30), {"updated": "2026-10-27"}, {"updated": None}),
            (ListFilters(min_likes=100), {"likes": 100}, {"likes": 99}),
            (ListFilters(min_downloads=1000), {"downloads": 5000}, {"downloads": 999}),
            (
                ListFilters(min_lifetime_downloads=1_000_000),
                {"lifetime": 1_000_000},
                {"lifetime": 999_999, "downloads": 10**7},
            ),
        ]
        for filters, kept, dropped in cases:
            with self.subTest(filters.summary()):
                self.assertTrue(self.accepts(filters, **kept))
                self.assertFalse(self.accepts(filters, **dropped))
        self.assertTrue(self.accepts(ListFilters()))  # no filters: everything

    def test_uncensored_hidden_shown_or_only(self):
        hidden, shown, only = (ListFilters(uncensored=u) for u in (HIDDEN, SHOWN, ONLY))
        self.assertFalse(self.accepts(hidden, uncensored=True))
        self.assertTrue(self.accepts(shown, uncensored=True))
        self.assertTrue(self.accepts(shown))
        self.assertTrue(self.accepts(only, uncensored=True))
        self.assertFalse(self.accepts(only))

    def test_summary_showing_and_clearing(self):
        filters = ListFilters(
            sizes=frozenset({"122B", "35B"}),
            quants=frozenset({"16", "8", "5-6", "4"}),
            updated_days=90,
            min_downloads=10_000,
            uncensored=ONLY,
        )
        self.assertEqual(
            filters.summary(),
            "size 35B or 122B; 4 bits or more; updated in the last 90 days; "
            "10,000 or more downloads in 30 days; only uncensored",
        )
        showing = dict(filters.showing())
        self.assertEqual(showing["Model size"], "35B, 122B")
        self.assertEqual(showing["Quant"], "4 bits or more")
        self.assertEqual(showing["Updated"], "in the last 90 days")
        self.assertEqual(showing["Source"], "any")
        self.assertEqual(len(filters.showing()), 11)
        self.assertEqual(showing["Downloads (lifetime)"], "any")
        lifetime = ListFilters(min_lifetime_downloads=100_000)
        self.assertEqual(lifetime.summary(), "100,000 or more downloads in all")
        self.assertEqual(ListFilters().summary(), "none")
        self.assertFalse(filters.is_default)
        self.assertEqual(filters.cleared(), ListFilters())  # "only" ends with the filters
        self.assertEqual(
            replace(filters, uncensored=SHOWN).cleared(), ListFilters(uncensored=SHOWN)
        )
        self.assertTrue(ListFilters(uncensored=SHOWN).is_default)  # the saved setting

    def test_updated_on_reads_the_repo_date(self):
        self.assertEqual(
            updated_on(entry(updated="2026-09-30T08:07:53.000Z")), datetime.date(2026, 9, 30)
        )
        self.assertIsNone(updated_on(entry(updated=None)))
        self.assertIsNone(updated_on(entry(updated="not a date")))


class SortTest(unittest.TestCase):
    def names(self, sorts, label, rows):
        sort = next(s for s in sorts if s.label == label)
        return [r.name for r in sort_rows(rows, sort)]

    def test_plain_sorts_order_by_their_value_alone(self):
        rows = [
            row("best", memory=40, file_gib=30, likes=10, downloads=100),
            row("small", memory=10, file_gib=5, likes=500, downloads=9),
            row("huge", Fit.TOO_BIG, memory=300, file_gib=250, likes=900, downloads=10**6),
            row("popular", memory=20, file_gib=15, likes=50, downloads=10**5),
        ]
        for sorts in (BEST_FIT_SORTS, ALL_MODELS_SORTS):
            names = lambda label, s=sorts: self.names(s, label, rows)  # noqa: E731
            self.assertEqual(
                names("download size, largest first"), ["huge", "best", "popular", "small"]
            )
            self.assertEqual(names("likes, most first"), ["huge", "small", "popular", "best"])
            self.assertEqual(
                names("downloads (30 days), most first"), ["huge", "popular", "best", "small"]
            )
        names = lambda label: self.names(ALL_MODELS_SORTS, label, rows)  # noqa: E731
        self.assertEqual(
            names("memory needed, largest first"), ["huge", "best", "popular", "small"]
        )
        self.assertEqual(
            names("memory needed, smallest first"), ["small", "popular", "best", "huge"]
        )
        best = lambda label: self.names(BEST_FIT_SORTS, label, rows)  # noqa: E731
        self.assertEqual(best("best first"), ["best", "small", "huge", "popular"])  # as ranked

    def test_quant_sort_goes_by_bits_per_weight_across_sizes_and_formats(self):
        rows = [
            row("35B Q4_K_M", params=35, file_gib=21.1),  # about 5.2 bits
            row("9B Q8_0", params=9, file_gib=8.9),  # about 8.5 bits
            row("122B NVFP4", params=122, file_gib=70.0),  # about 4.9 bits
            row("397B IQ1_S", Fit.TOO_BIG, params=397, file_gib=76.2),  # about 1.6 bits
            row("9B Q2_K", params=9, file_gib=3.4),  # about 3.2 bits
        ]
        self.assertEqual(
            self.names(ALL_MODELS_SORTS, "quant, smallest first (bits per weight)", rows),
            ["397B IQ1_S", "9B Q2_K", "122B NVFP4", "35B Q4_K_M", "9B Q8_0"],
        )

    def test_model_order_newest_then_largest_then_finest(self):
        rows = [
            row("1.0 35B Q8", version="1.0", params=35, file_gib=36),
            row("1.5 9B Q8", params=9, file_gib=9),
            row("1.5 397B Q4", params=397, file_gib=228),
            row("1.5 397B Q8", params=397, file_gib=399),
            row("unknown 9B", version=None, params=9, file_gib=9),
            row("1.5 397B Q8 community", params=397, file_gib=399, source=Source.COMMUNITY),
        ]
        self.assertEqual(
            self.names(ALL_MODELS_SORTS, "by model, newest and largest first", rows),
            ["1.5 397B Q8", "1.5 397B Q8 community", "1.5 397B Q4", "1.5 9B Q8", "1.0 35B Q8",
             "unknown 9B"],
        )  # fmt: skip
        self.assertEqual(
            self.names(ALL_MODELS_SORTS, "model size, smallest first", rows)[:2],
            ["1.5 9B Q8", "unknown 9B"],
        )

    def test_lifetime_downloads_sort_apart_from_the_30_day_count(self):
        rows = [
            row("old favourite", downloads=10, lifetime=9_000_000),
            row("new hit", downloads=500_000, lifetime=600_000),
            row("quiet", downloads=5, lifetime=50),
        ]
        for sorts in (BEST_FIT_SORTS, ALL_MODELS_SORTS):
            self.assertEqual(
                self.names(sorts, "downloads (lifetime), most first", rows),
                ["old favourite", "new hit", "quiet"],
            )
            self.assertEqual(
                self.names(sorts, "downloads (30 days), most first", rows),
                ["new hit", "old favourite", "quiet"],
            )

    def test_updated_newest_first_puts_unknown_dates_last(self):
        rows = [
            row("old", updated="2026-01-01"),
            row("none", updated=None),
            row("new", updated="2026-10-20"),
        ]
        self.assertEqual(
            self.names(BEST_FIT_SORTS, "updated, newest first", rows), ["new", "old", "none"]
        )

    def test_only_the_best_fit_list_offers_advice(self):
        self.assertEqual([s.advice for s in BEST_FIT_SORTS[:2]], [True, True])
        self.assertFalse(any(s.advice for s in BEST_FIT_SORTS[2:]))
        self.assertFalse(any(s.advice for s in ALL_MODELS_SORTS))
        self.assertNotIn("score", {s.by for s in ALL_MODELS_SORTS})


class ListsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        families = load_families()
        found = entries(official_catalog(), families, load_rules())
        candidates = build_candidates(found, MACHINES["spark"], load_calibration(), load_tested())
        cls.ranked = rank(candidates, families)

    def test_best_fit_and_all_models(self):
        lists = build_lists(self.ranked, ListFilters(), "", TODAY)
        self.assertTrue(all(c.fit is not Fit.TOO_BIG for c in lists.best_fit))
        self.assertTrue(any(c.fit is Fit.TOO_BIG for c in lists.all_models))
        self.assertEqual(lists.too_big, len(lists.all_models) - len(lists.best_fit))
        self.assertEqual(lists.total, len(lists.all_models))
        files = {(c.entry.repo.id, c.entry.file.path) for c in lists.all_models}
        self.assertLessEqual(files, set(lists.recommended))  # every row has a starting setting
        self.assertEqual(len(files), len(lists.all_models))  # one row per file

    def test_all_models_rows_are_each_file_at_its_lightest_setting(self):
        for c in lightest(self.ranked).values():
            same = [
                o.estimate.total
                for o in self.ranked
                if o.entry.file.path == c.entry.file.path and o.kv_type == "f16"
            ]
            self.assertEqual(c.estimate.total, min(same))
            self.assertEqual(c.context, 32768)
            self.assertFalse(c.vision)

    def test_recommended_rows_are_the_best_fitting_setting(self):
        picks = recommended(self.ranked)
        first = next(iter(picks.values()))
        self.assertEqual(first.entry.file.name, "Ornith-1.5-35B-Q8_0.gguf")
        self.assertEqual(first.context, first.native)
        orders = [c.fit is Fit.TOO_BIG for c in picks.values()]
        self.assertEqual(orders, sorted(orders))  # files that fit come first

    def test_filters_and_search_narrow_the_rows_and_the_counts(self):
        filters = ListFilters(sizes=frozenset({"397B"}))
        lists = build_lists(self.ranked, filters, "", TODAY)
        self.assertEqual(len(lists.all_models), 4)
        self.assertEqual(lists.total, 21)
        searched = build_lists(self.ranked, ListFilters(), "Q8_0 397B", TODAY)
        self.assertEqual(len(searched.all_models), 1)
        self.assertEqual((len(searched.best_fit), searched.too_big), (0, 1))
        rows = list(lightest(self.ranked).values())
        self.assertEqual(count_matching(rows, filters, TODAY), (4, 2))
        self.assertEqual(count_matching(rows, ListFilters(), TODAY), (21, 18))

    def test_uncensored_files_follow_the_filter(self):
        def count(mode):
            return len(build_lists(self.ranked, ListFilters(uncensored=mode), "", TODAY).all_models)

        self.assertEqual(count(SHOWN), count(HIDDEN) + count(ONLY))
        self.assertGreater(count(ONLY), 0)


if __name__ == "__main__":
    unittest.main()
