from __future__ import annotations

import copy
import unittest

from ornith_launcher.catalog import gguf
from ornith_launcher.catalog.arch import ModelArch
from ornith_launcher.errors import DataFileError
from ornith_launcher.planner import memory_model as mm
from ornith_launcher.planner.memory_model import GIB, KIB, MIB, Calibration
from ornith_launcher.resources import load_json
from tests.gguf_builder import gguf_bytes, ornith_pairs


def arch_for(size: str, nextn: bool = True) -> ModelArch:
    values = gguf.parse_metadata(gguf_bytes(ornith_pairs(size, nextn=nextn))).values
    return ModelArch.from_metadata(values)


def dense_arch() -> ModelArch:
    """A plain transformer: every layer has attention, no SSM state."""
    return ModelArch(
        architecture="llama",
        block_count=32,
        nextn_layers=0,
        embedding_length=4096,
        context_length=131072,
        head_count_kv=8,
        key_length=128,
        value_length=128,
        full_attention_interval=None,
        expert_count=0,
        ssm_state_size=0,
        ssm_inner_size=0,
        ssm_group_count=0,
        ssm_time_step_rank=0,
        ssm_conv_kernel=0,
        has_rope_scaling=False,
    )


class FormulaTest(unittest.TestCase):
    """Design 8.2: the formulas against llama.cpp fit-params (b11552, 2026-10-12)."""

    def assertClose(self, actual: float, expected: float, tolerance: float = 0.02):
        self.assertLessEqual(abs(actual - expected), tolerance * expected, (actual, expected))

    def test_kv_cache_per_token_in_f16(self):
        for size, kib in (("9B", 32), ("35B", 20), ("397B", 30)):
            with self.subTest(size=size):
                self.assertEqual(mm.kv_bytes_per_token(arch_for(size)), kib * KIB)

    def test_nextn_layer_does_not_change_the_kv_cache(self):
        with_nextn = mm.kv_cache_bytes(arch_for("35B", nextn=True), 262144)
        self.assertEqual(with_nextn, mm.kv_cache_bytes(arch_for("35B", nextn=False), 262144))

    def test_context_matches_fit_params(self):
        # fit-params "context" column = KV cache + recurrent state of one sequence.
        cases = {("35B", 262144): 5182, ("35B", 1048576): 20542, ("9B", 262144): 8242}
        for (size, context), fit_params_mib in cases.items():
            with self.subTest(size=size, context=context):
                arch = arch_for(size)
                total = mm.kv_cache_bytes(arch, context) + mm.recurrent_state_bytes(arch, 1)
                self.assertClose(total, fit_params_mib * MIB)

    def test_recurrent_state_per_sequence(self):
        for size, mib in (("9B", 50.25), ("35B", 62.81), ("397B", 186.33)):
            with self.subTest(size=size):
                self.assertAlmostEqual(mm.recurrent_state_bytes(arch_for(size), 1) / MIB, mib, 1)
        one = mm.recurrent_state_bytes(arch_for("35B"), 1)
        self.assertEqual(mm.recurrent_state_bytes(arch_for("35B"), 4), 4 * one)

    def test_compute_buffer_fit(self):
        calibration = mm.load_calibration()
        for context, formula_mib, fit_params_mib in ((262144, 1132, 1029), (1048576, 2222, 2172)):
            with self.subTest(context=context):
                compute = mm.compute_buffer_bytes(context, calibration)
                self.assertAlmostEqual(compute / MIB, formula_mib, delta=1)
                # The fit is conservative: never below llama.cpp's own figure.
                self.assertGreaterEqual(compute, fit_params_mib * MIB)

    def test_quantised_kv_cache(self):
        arch = arch_for("35B")
        self.assertEqual(mm.kv_cache_bytes(arch, 262144, "q8_0"), 2720 * MIB)
        self.assertEqual(mm.kv_cache_bytes(arch, 262144, "q4_0"), 1440 * MIB)
        with self.assertRaisesRegex(ValueError, "q3_x"):
            mm.kv_cache_bytes(arch, 262144, "q3_x")

    def test_dense_model_has_no_recurrent_state(self):
        arch = dense_arch()
        self.assertEqual(mm.recurrent_state_bytes(arch, 4), 0)
        self.assertEqual(mm.kv_bytes_per_token(arch), 32 * 8 * 256 * 2)


class EstimateTest(unittest.TestCase):
    """Design 8.3: whole-server estimates against peaks measured on the DGX Spark."""

    def setUp(self):
        self.calibration = mm.load_calibration()

    def test_matches_measured_peaks_without_underestimating(self):
        # 1.5 35B-A3B Q8_0 (35.21 GiB) + projector (0.84 GiB), 4 slots, and llama.cpp's
        # default 8 GiB prompt cache, which those runs used. Measured peaks minus the
        # ~5 GiB idle baseline.
        for context, measured_gib in ((262144, 51.4 - 5), (1048576, 70.0 - 5)):
            with self.subTest(context=context):
                estimate = mm.estimate_memory(
                    arch_for("35B"),
                    weights=round(35.21 * GIB),
                    mmproj=round(0.84 * GIB),
                    context=context,
                    calibration=self.calibration,
                    cache_ram=8192 * MIB,
                )
                self.assertGreaterEqual(estimate.total, measured_gib * GIB)
                self.assertLessEqual(estimate.total, measured_gib * GIB * 1.03)

    def test_parts_add_up(self):
        estimate = mm.estimate_memory(
            arch_for("9B"), weights=5 * GIB, context=65536, calibration=self.calibration, slots=1
        )
        self.assertEqual(estimate.total, estimate.weights + estimate.context_part)
        self.assertEqual(estimate.mmproj, 0)
        self.assertEqual(estimate.kv_cache, 65536 * 32 * KIB)

    def test_prompt_cache_is_bounded_by_cache_ram(self):
        kv = 20 * GIB
        self.assertEqual(
            mm.runtime_overhead_bytes(kv, 4096 * MIB, self.calibration),
            self.calibration.overhead_base + 4096 * MIB,
        )
        self.assertEqual(
            mm.runtime_overhead_bytes(kv, 0, self.calibration), self.calibration.overhead_base
        )
        self.assertEqual(
            mm.runtime_overhead_bytes(4 * GIB, 4096 * MIB, self.calibration),
            self.calibration.overhead_base + GIB,
        )

    def test_rejects_empty_context(self):
        with self.assertRaises(ValueError):
            mm.estimate_memory(arch_for("9B"), weights=1, context=0, calibration=self.calibration)


class CalibrationFileTest(unittest.TestCase):
    def setUp(self):
        self.data = load_json(mm.CALIBRATION_FILE)

    def broken(self, change) -> dict:
        data = copy.deepcopy(self.data)
        change(data)
        return data

    def test_shipped_values(self):
        calibration = mm.load_calibration()
        self.assertEqual(calibration.compute_base, 768 * MIB)
        self.assertEqual(calibration.overhead_base, 3 * GIB)
        self.assertEqual(calibration.prompt_cache_share_of_kv, 0.25)

    def test_every_constant_is_labelled(self):
        for section in ("compute", "runtime_overhead"):
            with self.subTest(section=section):
                self.assertIn(self.data[section]["level"], ("TESTED", "INFERRED", "ESTIMATED"))
                self.assertTrue(self.data[section]["source"])

    def test_rejects_bad_data(self):
        cases = {
            "malformed": lambda d: d.pop("compute"),
            "negative compute": lambda d: d["compute"].update(base_mib=-1),
            "negative overhead": lambda d: d["runtime_overhead"].update(base_mib=-1),
            "0 to 1": lambda d: d["runtime_overhead"].update(prompt_cache_share_of_kv=2),
        }
        for message, change in cases.items():
            with self.subTest(message), self.assertRaisesRegex(DataFileError, message):
                Calibration.from_dict(self.broken(change))


if __name__ == "__main__":
    unittest.main()
