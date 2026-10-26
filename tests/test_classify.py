from __future__ import annotations

import unittest

from ornith_launcher.catalog import gguf
from ornith_launcher.catalog.classify import (
    Badge,
    FileEvidence,
    Source,
    VersionSource,
    classify,
    load_rules,
)
from ornith_launcher.catalog.families import load_families
from ornith_launcher.catalog.gguf import ValueType
from tests.gguf_builder import gguf_bytes, ornith_pairs

FAMILIES = load_families()
RULES = load_rules()


def metadata(size: str, name: str = "", nextn: bool = True):
    return gguf.parse_metadata(gguf_bytes(ornith_pairs(size, name=name, nextn=nextn))).values


def run(size: str, evidence: FileEvidence, name: str = "", nextn: bool = True):
    return classify(metadata(size, name, nextn), evidence, FAMILIES, RULES)


class VersionAndSizeTest(unittest.TestCase):
    def test_official_repos(self):
        result = run(
            "35B", FileEvidence("ornith-1.0-35b-Q8_0.gguf", "ornith-ai/Ornith-1.0-35B-GGUF")
        )
        self.assertEqual(result.family.display_name, "Ornith 1.0 35B")
        self.assertEqual(result.version_source, VersionSource.OFFICIAL_REPO)
        self.assertEqual(result.source, Source.OFFICIAL)
        self.assertEqual(result.size_label, "35B")

        result = run(
            "35B", FileEvidence("Ornith-1.5-35B-Q8_0.gguf", "ornith-ai/Ornith-1.5-35B-A3B-GGUF")
        )
        self.assertEqual(result.size_label, "35B-A3B")  # same shape, named differently in 1.5

    def test_122b_exists_for_1_5_only(self):
        # The official 122B header names its Qwen base, not Ornith: the repo gives the version.
        official = run(
            "122B",
            FileEvidence("Ornith-1.5-122B-Q4_K_M.gguf", "ornith-ai/Ornith-1.5-122B-GGUF"),
            name="Qwen3.5-122B-A10B",
        )
        self.assertEqual(official.family.display_name, "Ornith 1.5 122B")
        self.assertEqual(official.version_source, VersionSource.OFFICIAL_REPO)
        self.assertNotIn(Badge.UNTESTED_LOCALLY, official.badges)
        old = run("122B", FileEvidence("Ornith-1.0-122B-Q4_K_M.gguf"))
        self.assertIn("no 122B model is known for Ornith 1.0", old.excluded)

    def test_same_shape_different_versions_come_from_metadata(self):
        # 1.0 and 1.5 headers are identical apart from the nextn layer (design 3.2).
        old = run("9B", FileEvidence("model.gguf"), name="Ornith 1.0 9B", nextn=False)
        new = run("9B", FileEvidence("model.gguf"), name="Ornith-1.5-9B")
        self.assertEqual((old.version, new.version), ("1.0", "1.5"))
        self.assertEqual(old.version_source, VersionSource.HEADER)
        self.assertEqual(old.source, Source.LOCAL)

    def test_lineage_beats_names(self):
        evidence = FileEvidence(
            "Ornith-1.5-35B-Q4_K_M.gguf",  # misleading file name
            "someone/Finetune-Coder-35B-GGUF",
            base_models=("deepreinforce-ai/Ornith-1.0-35B",),
        )
        result = run("35B", evidence)
        self.assertEqual(result.version, "1.0")
        self.assertEqual(result.version_source, VersionSource.LINEAGE)
        self.assertTrue(result.lineage_verified)
        self.assertEqual(result.source, Source.COMMUNITY)

    def test_repo_name_then_file_name(self):
        by_repo = run(
            "397B",
            FileEvidence("x-IQ1_S.gguf", "quant-uploader/deepreinforce-ai_Ornith-1.0-397B-GGUF"),
        )
        self.assertEqual(by_repo.version, "1.0")
        self.assertEqual(by_repo.version_source, VersionSource.REPO_NAME)
        self.assertFalse(by_repo.lineage_verified)

        by_file = run("9B", FileEvidence("ornith_1_5_9b.Q6_K.gguf"))
        self.assertEqual(by_file.version, "1.5")
        self.assertEqual(by_file.version_source, VersionSource.FILE_NAME)

    def test_no_version_signal_gives_version_unknown(self):
        result = run(
            "9B", FileEvidence("Ornith-9B-Blend-Q4_K_M.gguf", "merge-uploader/Ornith-9B-Blend-GGUF")
        )
        self.assertTrue(result.supported)
        self.assertIsNone(result.family)
        self.assertEqual(result.size_label, "9B")
        self.assertIn(Badge.VERSION_UNKNOWN, result.badges)

    def test_unknown_future_version_is_not_guessed(self):
        result = run("35B", FileEvidence("Ornith-2.0-35B-Q8_0.gguf", "someone/Ornith-2.0-35B-GGUF"))
        self.assertIsNone(result.version)
        self.assertIn(Badge.VERSION_UNKNOWN, result.badges)


class ExclusionTest(unittest.TestCase):
    def test_other_shapes_are_excluded_with_a_reason(self):
        pairs = ornith_pairs("9B")
        pairs = [
            ("qwen35.block_count", ValueType.UINT32, 5) if p[0] == "qwen35.block_count" else p
            for p in pairs
        ]
        values = gguf.parse_metadata(gguf_bytes(pairs)).values
        result = classify(values, FileEvidence("Ornith-1.5-9B-DFlash.gguf"), FAMILIES, RULES)
        self.assertFalse(result.supported)
        self.assertIn("unsupported shape", result.excluded)
        self.assertEqual(result.badges, ())

    def test_header_without_model_keys_is_excluded(self):
        values = gguf.parse_metadata(gguf_bytes([("general.name", ValueType.STRING, "x")])).values
        result = classify(values, FileEvidence("x.gguf"), FAMILIES, RULES)
        self.assertIn("not a recognised model", result.excluded)


class BadgesTest(unittest.TestCase):
    def test_397b_is_untested_locally_and_badges_stack(self):
        evidence = FileEvidence(
            "Ornith-1.5-397B-uncensored-IQ1_S.gguf",
            "lowbit-uploader/Ornith-1.5-397B-uncensored-GGUF",
        )
        result = run("397B", evidence)
        self.assertEqual(result.badges, (Badge.UNTESTED_LOCALLY, Badge.LOW_BIT, Badge.UNCENSORED))

    def test_uncensored_from_tags_or_header_name(self):
        by_tag = run("9B", FileEvidence("a-Q4_K_M.gguf", "x/Ornith-1.5-9B", tags=("abliterated",)))
        by_header = run("9B", FileEvidence("model.gguf"), name="Ornith 1.5 9B Heretic")
        self.assertTrue(by_tag.uncensored)
        self.assertTrue(by_header.uncensored)

    def test_other_spellings_are_uncensored(self):
        for name in ("Ornith-1.5-9B-OBLITERATED", "Ornith-1.0-35B-abliterix", "Ornith-1.5-9B-NSFW"):
            result = run("9B", FileEvidence(f"{name}-Q8_0.gguf", f"example-org/{name}-GGUF"))
            self.assertTrue(result.uncensored, name)

    def test_uncensored_from_the_models_it_was_made_from(self):
        # A quant whose own name hides it, made from an abliterated model.
        quant = FileEvidence(
            "m-Q4_K_M.gguf",
            "quant-uploader/Ornith-1.5-9B-Tuned-GGUF",
            base_models=("uncensored-uploader/Ornith-1.5-9B-abliterated",),
        )
        self.assertTrue(run("9B", quant).uncensored)
        # A file found on disk: only its header says so.
        values = dict(metadata("9B"))
        local = FileEvidence("model.gguf")
        for key, value in (
            ("general.tags", ("gguf", "uncensored")),
            ("general.finetune", "heretic"),
            ("general.base_model.0.repo_url", "https://huggingface.co/x/Ornith-9B-abliterated"),
        ):
            result = classify({**values, key: value}, local, FAMILIES, RULES)
            self.assertTrue(result.uncensored, key)
        plain = {**values, "general.tags": ("gguf",), "general.base_model.0.name": "Ornith 1.5 9B"}
        self.assertFalse(classify(plain, local, FAMILIES, RULES).uncensored)

    def test_every_uncensored_keyword_marks_a_file(self):
        for keyword in RULES.uncensored_keywords:
            name = f"Ornith-1.5-9B-{keyword.upper()}-Q8_0.gguf"
            result = run("9B", FileEvidence(name, "example-org/Ornith-1.5-9B-GGUF"))
            self.assertTrue(result.uncensored, keyword)
        plain = run("9B", FileEvidence("Ornith-1.5-9B-Q8_0.gguf", "example-org/Ornith-1.5-9B-GGUF"))
        self.assertFalse(plain.uncensored)

    def test_official_35b_has_no_badges(self):
        result = run(
            "35B", FileEvidence("Ornith-1.5-35B-Q8_0.gguf", "ornith-ai/Ornith-1.5-35B-A3B-GGUF")
        )
        self.assertEqual(result.badges, ())
        self.assertEqual(result.quant, "Q8_0")
        self.assertFalse(result.low_bit)


if __name__ == "__main__":
    unittest.main()
