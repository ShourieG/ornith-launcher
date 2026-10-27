from __future__ import annotations

import unittest

from ornith_launcher.planner.memory_model import load_calibration
from ornith_launcher.planner.rank import Filters, build_candidates, load_tested, rank
from ornith_launcher.planner.score import (
    WEIGHTS,
    Scorer,
    context_part,
    memory_part,
    quant_part,
    size_part,
)
from tests.test_rank import ENTRIES, FAMILIES, MACHINES


def scored(machine: str):
    candidates = build_candidates(ENTRIES, MACHINES[machine], load_calibration(), load_tested())
    listed = Filters(show_uncensored=False)
    ranked = [c for c in rank(candidates, FAMILIES) if listed.accepts(c)]
    scorer = Scorer.for_candidates(ranked)
    return ranked, scorer


class ScoreTest(unittest.TestCase):
    def test_weights_add_up_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0)

    def test_quant_quality_falls_steeply_below_four_bits(self):
        self.assertEqual(quant_part(8.5), 1.0)
        self.assertAlmostEqual(quant_part(6.0), 0.95)
        self.assertAlmostEqual(quant_part(4.0), 0.70)
        self.assertAlmostEqual(quant_part(3.0), 0.45)
        self.assertLess(quant_part(1.6), 0.15)
        values = [quant_part(b / 10) for b in range(0, 100)]
        self.assertEqual(values, sorted(values))  # never lower for more bits

    def test_parts_for_the_spark_rows(self):
        ranked, scorer = scored("spark")
        by_context = {c.context: c for c in ranked if c.entry.classification.quant == "Q8_0"}
        self.assertEqual(context_part(by_context[262144]), 1.0)  # native
        self.assertEqual(context_part(by_context[524288]), 0.9)  # YaRN
        self.assertEqual(context_part(by_context[32768]), 0.2)  # short for coding agents
        top = ranked[0]
        self.assertGreater(memory_part(top), 0)
        sizes = {c.entry.classification.shape.label: size_part(c) for c in ranked}
        self.assertLess(sizes["9B"], sizes["35B"])
        self.assertLess(sizes["35B"], sizes["122B"])
        summary = scorer.score(top).summary()
        self.assertRegex(summary, r"^score \d+: memory 0\.\d\d, quant \d\.\d\d, size")
        self.assertTrue(summary.endswith("(a heuristic, not measured)"))

    def test_a_small_laptop_gets_the_newest_9b_at_a_usable_context(self):
        ranked, scorer = scored("laptop")
        fitting = [c for c in ranked if c.fits]
        top = max(fitting, key=lambda c: scorer.score(c).total)
        self.assertEqual(top.entry.classification.shape.label, "9B")
        self.assertEqual(top.entry.classification.version, "1.5")  # newest over 1.0
        self.assertGreaterEqual(top.context, 65536)  # 32k only when nothing longer fits

    def test_popularity_is_relative_to_the_list(self):
        ranked, scorer = scored("spark")
        most = max(ranked, key=lambda c: c.entry.repo.downloads)
        if most.entry.repo.downloads:
            self.assertGreater(scorer.popularity_part(most), 0.5)
        self.assertEqual(Scorer().popularity_part(most), 0.0)  # nothing to compare with


if __name__ == "__main__":
    unittest.main()
