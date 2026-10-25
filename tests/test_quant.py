from __future__ import annotations

import unittest

from ornith_launcher.catalog.quant import is_low_bit, quant_from_file_type, quant_from_name

LOW_BIT = ("IQ1", "IQ2", "Q1_", "Q2_", "TQ1", "TQ2")


class QuantFromNameTest(unittest.TestCase):
    def test_file_names_seen_on_hugging_face(self):
        cases = {
            "ornith-1.0-9b-Q4_K_M.gguf": "Q4_K_M",
            "ornith-1.0-9b-bf16.gguf": "BF16",
            "Ornith-1.5-35B-Q8_0.gguf": "Q8_0",
            "Ornith-1.5-397B-IQ1_S.gguf": "IQ1_S",
            "Ornith-1.5-397B-IQ2_XXS.gguf": "IQ2_XXS",
            "Ornith-1.5-35B-A3B-UNCENSORED-CRACK-Q8_0.gguf": "Q8_0",
            "Ornith-1.5-9B-uncensored.i1-Q4_K_S.gguf": "Q4_K_S",
            "UD-IQ1_S/Ornith-1.0-397B-UD-IQ1_S-00001-of-00003.gguf": "UD-IQ1_S",
            "Ornith-1.0-397B-UD-Q2_K_XL.gguf": "UD-Q2_K_XL",
            "ornith-35b-mxfp4_moe.gguf": "MXFP4_MOE",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(quant_from_name(name), expected)

    def test_names_without_a_quant(self):
        for name in ("model.gguf", "mmproj-Ornith-1.5-397B.gguf", "Ornith-1.5-35B-A3B.gguf"):
            with self.subTest(name=name):
                self.assertIsNone(quant_from_name(name))


class QuantFromFileTypeTest(unittest.TestCase):
    def test_known_and_unknown_values(self):
        self.assertEqual(quant_from_file_type(15), "Q4_K_M")
        self.assertEqual(quant_from_file_type(7), "Q8_0")
        self.assertEqual(quant_from_file_type(24), "IQ1_S")
        self.assertIsNone(quant_from_file_type(4))  # removed from llama.cpp
        self.assertIsNone(quant_from_file_type(None))
        self.assertIsNone(quant_from_file_type(True))


class LowBitTest(unittest.TestCase):
    def test_low_bit_quants(self):
        for quant in (
            "IQ1_S",
            "IQ1_M",
            "IQ2_XXS",
            "Q2_K",
            "Q2_K_S",
            "UD-IQ1_S",
            "UD-Q2_K_XL",
            "TQ1_0",
        ):
            with self.subTest(quant=quant):
                self.assertTrue(is_low_bit(quant, LOW_BIT))

    def test_three_bits_and_above_are_not_low_bit(self):
        for quant in ("IQ3_XXS", "Q3_K_M", "Q4_K_M", "IQ4_XS", "Q8_0", "BF16", "UD-IQ3_XXS", None):
            with self.subTest(quant=quant):
                self.assertFalse(is_low_bit(quant, LOW_BIT))


if __name__ == "__main__":
    unittest.main()
