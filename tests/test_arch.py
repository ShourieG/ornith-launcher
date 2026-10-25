from __future__ import annotations

import unittest

from ornith_launcher.catalog import gguf
from ornith_launcher.catalog.arch import ModelArch
from ornith_launcher.catalog.gguf import GGUFError, ValueType
from tests.gguf_builder import gguf_bytes, ornith_pairs


def arch_for(pairs) -> ModelArch:
    return ModelArch.from_metadata(gguf.parse_metadata(gguf_bytes(pairs)).values)


class ModelArchTest(unittest.TestCase):
    def test_layer_counts_match_the_real_headers(self):
        # (size, nextn layer present) -> (main layers, attention layers); values from design 3.2.
        cases = {
            ("9B", True): (32, 8),
            ("9B", False): (32, 8),
            ("35B", True): (40, 10),
            ("35B", False): (40, 10),
            ("397B", True): (60, 15),
        }
        for (size, nextn), (main, attention) in cases.items():
            with self.subTest(size=size, nextn=nextn):
                arch = arch_for(ornith_pairs(size, nextn=nextn))
                self.assertEqual(arch.main_layers, main)
                self.assertEqual(arch.attention_layers, attention)
                self.assertEqual(arch.nextn_layers, 1 if nextn else 0)

    def test_reads_moe_and_ssm_fields(self):
        arch = arch_for(ornith_pairs("397B"))
        self.assertEqual(arch.expert_count, 512)
        self.assertEqual(arch.ssm_inner_size, 8192)
        self.assertEqual(arch.ssm_time_step_rank, 64)
        self.assertEqual(arch.head_count_kv, 2)
        self.assertFalse(arch.has_rope_scaling)
        self.assertEqual(arch_for(ornith_pairs("9B")).expert_count, 0)

    def test_detects_rope_scaling_set_by_the_file(self):
        pairs = ornith_pairs("35B") + [("qwen35moe.rope.scaling.factor", ValueType.FLOAT32, 4.0)]
        self.assertTrue(arch_for(pairs).has_rope_scaling)

    def test_missing_required_key_is_an_error(self):
        pairs = [p for p in ornith_pairs("9B") if p[0] != "qwen35.embedding_length"]
        with self.assertRaisesRegex(GGUFError, "embedding_length"):
            arch_for(pairs)
        with self.assertRaisesRegex(GGUFError, "general.architecture"):
            ModelArch.from_metadata({})

    def test_per_layer_arrays_use_the_largest_value(self):
        key = "qwen35.attention.head_count_kv"
        per_layer = (key, ValueType.ARRAY, (ValueType.UINT32, [0, 4, 0, 4]))
        pairs = [per_layer if pair[0] == key else pair for pair in ornith_pairs("9B")]
        self.assertEqual(arch_for(pairs).head_count_kv, 4)
        # The same header read back from the JSON catalog cache holds a list.
        values = dict(gguf.parse_metadata(gguf_bytes(pairs)).values)
        values[key] = [0, 4, 0, 4]
        self.assertEqual(ModelArch.from_metadata(values).head_count_kv, 4)
        values[key] = "four"
        with self.assertRaisesRegex(GGUFError, "unexpected value"):
            ModelArch.from_metadata(values)


if __name__ == "__main__":
    unittest.main()
