from __future__ import annotations

import copy
import unittest

from ornith_launcher.catalog import gguf
from ornith_launcher.catalog.arch import ModelArch
from ornith_launcher.catalog.families import FAMILIES_FILE, Families, load_families, version_key
from ornith_launcher.errors import DataFileError
from ornith_launcher.resources import load_json
from tests.gguf_builder import gguf_bytes, ornith_pairs


def arch_for(size: str, nextn: bool = True) -> ModelArch:
    values = gguf.parse_metadata(gguf_bytes(ornith_pairs(size, nextn=nextn))).values
    return ModelArch.from_metadata(values)


class ShippedFamiliesTest(unittest.TestCase):
    def setUp(self):
        self.families = load_families()

    def test_every_published_size_is_known(self):
        self.assertEqual(self.families.versions, ("1.5", "1.0"))  # newest first
        for version in self.families.versions:
            for shape in ("qwen35-9b", "qwen35moe-35b", "qwen35moe-397b"):
                with self.subTest(version=version, shape=shape):
                    self.assertIsNotNone(self.families.find(version, shape))
        # 122B was released with 1.5 only (2026-10-21).
        self.assertIsNotNone(self.families.find("1.5", "qwen35moe-122b"))
        self.assertIsNone(self.families.find("1.0", "qwen35moe-122b"))

    def test_shapes_match_headers_with_and_without_the_nextn_layer(self):
        for size, shape_id in (
            ("9B", "qwen35-9b"),
            ("35B", "qwen35moe-35b"),
            ("122B", "qwen35moe-122b"),
            ("397B", "qwen35moe-397b"),
        ):
            for nextn in (True, False):
                with self.subTest(size=size, nextn=nextn):
                    self.assertEqual(self.families.match_shape(arch_for(size, nextn)).id, shape_id)

    def test_only_the_397b_families_are_untested_locally(self):
        untested = {f.display_name for f in self.families.families if f.untested_locally}
        self.assertEqual(untested, {"Ornith 1.0 397B", "Ornith 1.5 397B"})

    def test_repo_lookups_ignore_case_and_cover_old_org_name(self):
        self.assertEqual(
            self.families.by_official_repo("ornith-ai/ornith-1.5-35b-a3b-gguf").display_name,
            "Ornith 1.5 35B-A3B",
        )
        self.assertEqual(
            self.families.by_lineage_repo("deepreinforce-ai/Ornith-1.0-397B").display_name,
            "Ornith 1.0 397B",
        )
        self.assertIsNone(self.families.by_lineage_repo("someone/else"))
        self.assertTrue(self.families.is_official_owner("DeepReinforce-AI/x"))
        self.assertFalse(self.families.is_official_owner("bartowski/Ornith-1.5-397B-GGUF"))


class FamiliesValidationTest(unittest.TestCase):
    def setUp(self):
        self.data = load_json(FAMILIES_FILE)

    def broken(self, change) -> dict:
        data = copy.deepcopy(self.data)
        change(data)
        return data

    def test_rejects_bad_data(self):
        cases = {
            "unknown shape": lambda d: d["families"][0].update(shape="nope"),
            "invalid yarn": lambda d: d["families"][0].update(yarn="maybe"),
            "listed twice": lambda d: d["families"].append(copy.deepcopy(d["families"][0])),
            "non-numeric": lambda d: d["families"][0].update(version="1.x"),
            "malformed": lambda d: d.pop("shapes"),
        }
        for message, change in cases.items():
            with self.subTest(message), self.assertRaisesRegex(DataFileError, message):
                Families.from_dict(self.broken(change))


class VersionKeyTest(unittest.TestCase):
    def test_numeric_ordering(self):
        self.assertEqual(
            sorted(["1.10", "1.5", "1.0", "2.0"], key=version_key), ["1.0", "1.5", "1.10", "2.0"]
        )


if __name__ == "__main__":
    unittest.main()
