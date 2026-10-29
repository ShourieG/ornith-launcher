from __future__ import annotations

import dataclasses
import unittest

from ornith_launcher.catalog.catalog import entries
from ornith_launcher.catalog.classify import load_rules
from ornith_launcher.catalog.families import load_families
from ornith_launcher.planner.memory_model import load_calibration
from ornith_launcher.planner.rank import (
    Filters,
    Fit,
    Level,
    Placement,
    build_candidates,
    load_tested,
    quant_rank,
    rank,
    suggest,
)
from ornith_launcher.system.memory import Pool, PoolKind
from tests.catalog_fixtures import official_catalog

GIB = 1024**3
FAMILIES = load_families()
ENTRIES = entries(official_catalog(), FAMILIES, load_rules())

# Synthetic machines from design 16.1.
MACHINES = {
    "laptop": [Pool(PoolKind.RAM, "RAM", 16 * GIB, 4 * GIB)],
    "gpu24_ram64": [
        Pool(PoolKind.VRAM, "GPU", 24 * GIB, GIB // 2),
        Pool(PoolKind.RAM, "RAM", 64 * GIB, 4 * GIB),
    ],
    "spark": [Pool(PoolKind.UNIFIED, "Unified", int(121.7 * GIB), int(5.3 * GIB))],
    "gpu48_ram512": [
        Pool(PoolKind.VRAM, "GPU", 48 * GIB, GIB // 2),
        Pool(PoolKind.RAM, "RAM", 512 * GIB, 8 * GIB),
    ],
    "two_gpu80_ram512": [
        Pool(PoolKind.VRAM, "GPU0", 80 * GIB, GIB // 2),
        Pool(PoolKind.VRAM, "GPU1", 80 * GIB, GIB // 2),
        Pool(PoolKind.RAM, "RAM", 512 * GIB, 8 * GIB),
    ],
}


def ranked_for(machine: str, entries_=ENTRIES):
    candidates = build_candidates(entries_, MACHINES[machine], load_calibration(), load_tested())
    return rank(candidates, FAMILIES)


def describe(candidate) -> tuple:
    return (
        candidate.entry.repo.id,
        candidate.entry.classification.quant,
        candidate.context,
        candidate.placement,
    )


class SuggestionTest(unittest.TestCase):
    def test_spark_gets_the_tested_35b_at_native_context(self):
        ranked = ranked_for("spark")
        suggestions = suggest(ranked, FAMILIES)
        top = suggestions[0].candidate
        self.assertEqual(
            describe(top),
            ("ornith-ai/Ornith-1.5-35B-A3B-GGUF", "Q8_0", 262144, Placement.SINGLE),
        )
        self.assertTrue(top.vision)
        self.assertEqual(top.level, Level.INFERRED)
        self.assertEqual(top.file_level, Level.TESTED)
        self.assertIn("inferred from tests on DGX Spark", suggestions[0].reason)

    def test_spark_never_gets_a_397b_suggestion(self):
        suggestions = suggest(ranked_for("spark"), FAMILIES)
        sizes = {s.candidate.entry.classification.shape.label for s in suggestions}
        self.assertNotIn("397B", sizes)
        self.assertIn("122B", sizes)
        # The low-bit 397B fits the Spark and stays in the full list.
        low_bit = [
            c
            for c in ranked_for("spark")
            if c.fits and c.entry.file.name == "Ornith-1.5-397B-IQ1_S.gguf"
        ]
        self.assertTrue(low_bit)
        self.assertIn("LOW-BIT", low_bit[0].badges)

    def test_spark_offers_a_long_context_row(self):
        suggestions = suggest(ranked_for("spark"), FAMILIES)
        long_rows = [s for s in suggestions if s.candidate.context > 262144]
        self.assertEqual(len(long_rows), 1)
        self.assertEqual(long_rows[0].candidate.yarn_factor, 2)
        self.assertIn("YaRN x2", long_rows[0].reason)

    def test_small_laptop_gets_a_9b_without_vision(self):
        suggestions = suggest(ranked_for("laptop"), FAMILIES)
        self.assertTrue(suggestions)
        top = suggestions[0].candidate
        self.assertEqual(top.entry.repo.id, "ornith-ai/Ornith-1.5-9B-GGUF")
        self.assertFalse(top.vision)
        self.assertEqual(top.context, 65536)
        self.assertIn("vision off", suggestions[0].reason)
        for s in suggestions:
            self.assertEqual(s.candidate.entry.classification.shape.label, "9B")
        # 32k never displaces a longer context that fits, but it is the lowest-memory row.
        lowest = next(s for s in suggestions if s.reason.startswith("Lowest memory"))
        self.assertEqual(lowest.candidate.context, 32768)
        self.assertIn("32k context", lowest.reason)

    def test_32k_comes_after_every_longer_context_that_fits(self):
        ranked = ranked_for("spark")
        fitting = [c for c in ranked if c.fits]
        first_short = next(i for i, c in enumerate(fitting) if c.context == 32768)
        self.assertTrue(all(c.context == 32768 for c in fitting[first_short:]))

    def test_discrete_gpu_offloads_the_35b_experts(self):
        top = suggest(ranked_for("gpu24_ram64"), FAMILIES)[0].candidate
        self.assertEqual(top.placement, Placement.MOE_OFFLOAD)
        self.assertEqual(top.entry.classification.shape.label, "35B")
        self.assertEqual(top.level, Level.ESTIMATED)  # measured only on unified memory
        vram, ram = top.needs
        self.assertLess(vram.need, 20 * GIB)
        self.assertGreater(ram.need, 33 * GIB)

    def test_large_workstation_prefers_the_397b_at_a_good_quant(self):
        suggestions = suggest(ranked_for("gpu48_ram512"), FAMILIES)
        top = suggestions[0]
        cls = top.candidate.entry.classification
        self.assertEqual(top.candidate.entry.repo.id, "ornith-ai/Ornith-1.5-397B-GGUF")
        self.assertGreaterEqual(quant_rank(cls.quant, cls.low_bit), quant_rank("Q4_K_M", False))
        self.assertEqual(top.candidate.placement, Placement.MOE_OFFLOAD)
        self.assertIn("untested locally", top.reason)

    def test_two_gpus_are_planned_together(self):
        top = suggest(ranked_for("two_gpu80_ram512"), FAMILIES)[0].candidate
        self.assertEqual(top.placement, Placement.MULTI_GPU)
        self.assertEqual(top.needs[0].pool.name, "2 GPUs")

    def test_at_most_six_distinct_rows(self):
        for machine in MACHINES:
            with self.subTest(machine=machine):
                suggestions = suggest(ranked_for(machine), FAMILIES)
                self.assertLessEqual(len(suggestions), 6)
                keys = [describe(s.candidate) + (s.candidate.vision,) for s in suggestions]
                self.assertEqual(len(keys), len(set(keys)))

    def test_never_suggested(self):
        for machine in MACHINES:
            for s in suggest(ranked_for(machine), FAMILIES):
                c = s.candidate
                with self.subTest(machine=machine, file=c.entry.file.name):
                    self.assertTrue(c.fits)
                    self.assertEqual(c.kv_type, "f16")
                    self.assertNotEqual(c.entry.classification.quant, "BF16")
                    self.assertFalse(c.entry.classification.low_bit)
                    self.assertFalse(c.entry.classification.uncensored)
                    self.assertIsNotNone(c.entry.classification.version)

    def test_uncensored_only_after_opting_in(self):
        ranked = ranked_for("spark")
        shown = suggest(ranked, FAMILIES, show_uncensored=True)
        hidden = suggest(ranked, FAMILIES)
        self.assertFalse(any(s.candidate.entry.classification.uncensored for s in hidden))
        # Opting in never pushes the official build off the top.
        self.assertEqual(shown[0].candidate, hidden[0].candidate)


class OrderingTest(unittest.TestCase):
    def test_newer_version_first_at_equal_size_and_quant(self):
        ranked = [
            c
            for c in ranked_for("spark")
            if c.entry.classification.quant == "Q8_0"
            and c.entry.classification.shape.label == "9B"
            and c.context == 262144
            and c.kv_type == "f16"
            and not c.vision
            and c.entry.classification.version is not None
        ]
        versions = [c.entry.classification.version for c in ranked]
        self.assertEqual(versions[0], "1.5")
        self.assertLess(versions.index("1.5"), versions.index("1.0"))

    def test_quant_before_context(self):
        rows = [
            c
            for c in ranked_for("spark")
            if c.entry.repo.id == "ornith-ai/Ornith-1.5-9B-GGUF" and c.vision and c.kv_type == "f16"
        ]
        first = rows[0]
        self.assertEqual((first.entry.classification.quant, first.context), ("Q8_0", 262144))
        quants = [c.entry.classification.quant for c in rows]
        self.assertLess(quants.index("Q8_0"), quants.index("Q6_K"))
        self.assertEqual(quants[-1], "BF16")

    def test_context_order_after_quant(self):
        rows = [
            c.context
            for c in ranked_for("spark")
            if c.entry.file.name == "Ornith-1.5-9B-Q8_0.gguf" and c.vision and c.kv_type == "f16"
        ]
        self.assertEqual(rows, [262144, 524288, 1048576, 131072, 65536, 32768])

    def test_quant_rank(self):
        order = ["Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "IQ4_XS"]
        ranks = [quant_rank(q, False) for q in order]
        self.assertEqual(ranks, sorted(ranks, reverse=True))
        self.assertLess(quant_rank("IQ1_S", True), quant_rank("Q3_K_M", False))
        self.assertLess(quant_rank("BF16", False), quant_rank("IQ1_S", True))
        self.assertEqual(quant_rank("UD-Q4_K_M", False), quant_rank("Q4_K_M", False))


class CandidateTest(unittest.TestCase):
    def test_contexts_and_yarn(self):
        rows = [c for c in ranked_for("spark") if c.entry.file.name == "Ornith-1.0-9B-Q8_0.gguf"]
        contexts = {c.context: c for c in rows}
        self.assertEqual(sorted(contexts), [32768, 65536, 131072, 262144, 524288, 1048576])
        self.assertIsNone(contexts[262144].yarn_factor)
        self.assertEqual(contexts[1048576].yarn_factor, 4)
        self.assertIn("YaRN is not documented for Ornith 1.0", contexts[524288].notes)

    def test_file_with_its_own_rope_scaling_gets_no_yarn_flags(self):
        catalog = official_catalog()
        repo = catalog.repos["ornith-ai/Ornith-1.5-9B-GGUF"]
        header = dict(repo.header)
        header["qwen35.rope.scaling.factor"] = 4.0
        header["qwen35.context_length"] = 1048576
        scaled = type(repo)(**{**repo.__dict__, "header": header})
        catalog = type(catalog)(fetched_at=0, repos={scaled.id: scaled})
        rows = ranked_for("spark", entries(catalog, FAMILIES, load_rules()))
        self.assertTrue(rows)
        self.assertTrue(all(c.yarn_factor is None for c in rows))
        self.assertIn(1048576, {c.context for c in rows})

    def test_badges_and_shortfall(self):
        rows = ranked_for("laptop")
        big = next(c for c in rows if c.entry.file.name == "Ornith-1.5-35B-Q8_0.gguf")
        self.assertFalse(big.fits)
        self.assertRegex(big.shortfall, r"needs [\d.]+ GiB of RAM, you have [\d.]+ GiB to spare")
        mystery = next(c for c in rows if c.entry.repo.id == "someone/Ornith-9B-mystery-GGUF")
        self.assertIn("NO LICENCE", mystery.badges)
        self.assertIn("VERSION UNKNOWN", mystery.badges)
        q8_kv = next(c for c in rows if c.kv_type == "q8_0")
        self.assertIn("UNTESTED KV", q8_kv.badges)

    def test_fit_verdict_for_the_context_screen(self):
        def q4_122b(pools):
            candidates = build_candidates(
                [e for e in ENTRIES if e.repo.id == "ornith-ai/Ornith-1.5-122B-GGUF"],
                pools,
                load_calibration(),
                load_tested(),
                kv_types=["f16"],
            )
            q4 = [c for c in candidates if c.entry.classification.quant == "Q4_K_M" and c.vision]
            return {c.context: c for c in q4}

        spark = q4_122b(MACHINES["spark"])
        self.assertIs(spark[262144].fit, Fit.FITS)
        self.assertEqual(spark[262144].fit_reason, "")
        # 1M needs more than the plan's 85% but less than the whole pool: a stretch.
        self.assertIs(spark[1048576].fit, Fit.STRETCH)
        self.assertIn("into the 15% of Unified kept free", spark[1048576].fit_reason)
        # Fits the plan, but other apps hold the memory now: a stretch until they close.
        busy = [Pool(PoolKind.UNIFIED, "Unified", int(121.7 * GIB), int(5.3 * GIB), 50 * GIB)]
        self.assertIs(q4_122b(busy)[65536].fit, Fit.STRETCH)
        self.assertIn("close other apps", q4_122b(busy)[65536].fit_reason)
        # In the reserve and short of free memory: a Mac also says how much to free.
        self.assertNotIn("close other apps", q4_122b(busy)[1048576].fit_reason)
        mac = [dataclasses.replace(busy[0], apps_hint=True)]
        reason = q4_122b(mac)[1048576].fit_reason
        self.assertIn("into the 15% of Unified kept free", reason)
        self.assertRegex(reason, r"only 50.0 GiB is free right now: close other apps to free ")
        # More than the machine has at all: too big.
        small = q4_122b(MACHINES["laptop"])
        self.assertIs(small[65536].fit, Fit.TOO_BIG)
        self.assertIn("has 12.0 GiB at most", small[65536].fit_reason)

    def test_gated_repos_get_no_candidates(self):
        self.assertFalse([c for c in ranked_for("spark") if c.entry.repo.id.startswith("gated/")])

    def test_filters(self):
        rows = ranked_for("spark")
        only_15_35b = Filters(versions=frozenset({"1.5"}), sizes=frozenset({"35B"}))
        kept = [c for c in rows if only_15_35b.accepts(c)]
        self.assertTrue(kept)
        self.assertTrue(all(c.entry.classification.version == "1.5" for c in kept))
        self.assertTrue(all(c.kv_type == "f16" for c in kept))
        self.assertFalse(any(c.entry.classification.uncensored for c in kept))
        searched = [c for c in rows if Filters(search="quant-uploader iq1_s").accepts(c)]
        self.assertTrue(searched)
        self.assertTrue(all("IQ1_S" in c.entry.file.name for c in searched))
        no_low = Filters(show_low_bit=False, only_fitting=True)
        self.assertFalse(any(c.entry.classification.low_bit for c in rows if no_low.accepts(c)))


class TestedDataTest(unittest.TestCase):
    def test_shipped_results_match_known_files(self):
        tested = load_tested()
        self.assertIn("dgx-spark", tested.machines)
        self.assertEqual(
            tested.level(
                "ornith-ai/Ornith-1.5-35B-A3B-GGUF", "Ornith-1.5-35B-Q8_0.gguf", 1048576, "f16"
            ),
            Level.TESTED,
        )
        self.assertEqual(
            tested.level("ornith-ai/Ornith-1.5-9B-GGUF", "Ornith-1.5-9B-Q8_0.gguf", 262144, "f16"),
            Level.ESTIMATED,
        )
        self.assertEqual(
            tested.file_level("ornith-ai/Ornith-1.5-35B-A3B-GGUF", "Ornith-1.5-35B-Q8_0.gguf"),
            Level.TESTED,
        )


if __name__ == "__main__":
    unittest.main()
