from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from ornith_launcher.catalog import catalog as cat
from ornith_launcher.catalog.catalog import (
    Catalog,
    CatalogBuilder,
    FilePart,
    ModelFile,
    catalog_from_dict,
    catalog_to_dict,
    entries,
    group_files,
    header_subset,
    load_catalog,
    save_catalog,
)
from ornith_launcher.catalog.classify import Badge, Source, load_rules
from ornith_launcher.catalog.families import load_families
from ornith_launcher.catalog.gguf import ArraySummary, ValueType
from ornith_launcher.catalog.hfapi import HubError
from tests.catalog_fixtures import official_catalog
from tests.gguf_builder import gguf_bytes, ornith_pairs

FAMILIES = load_families()
RULES = load_rules()


def tree_item(path: str, size: int, oid: str | None = "0" * 64) -> dict:
    item = {"type": "file", "path": path, "size": size}
    if oid:
        item["lfs"] = {"oid": oid, "size": size}
    return item


class FakeHub:
    def __init__(self, searches, trees, headers, fail=()):
        self.searches = searches  # first query parameter value -> results
        self.trees = trees
        self.headers = headers
        self.fail = set(fail)
        self.tree_calls = []

    def list_models(self, params):
        return list(self.searches.get(params[0][1], []))

    def tree(self, repo_id, revision):
        self.tree_calls.append(repo_id)
        if repo_id in self.fail:
            raise HubError("boom")
        return self.trees[repo_id]

    def range_reader(self, repo_id, revision, path):
        data = self.headers[repo_id]
        return lambda size: data[:size]


def search_result(repo_id: str, sha: str = "s1", tags=(), gated=False) -> dict:
    return {
        "id": repo_id,
        "sha": sha,
        "gated": gated,
        "tags": ["gguf", *tags],
        "lastModified": "2026-10-20T00:00:00.000Z",
        "downloads": 5,
        "siblings": [{"rfilename": "model-Q8_0.gguf"}, {"rfilename": "README.md"}],
    }


class FileGroupingTest(unittest.TestCase):
    def test_split_parts_and_projectors(self):
        files, mmproj = group_files(
            [
                tree_item("README.md", 10, None),
                tree_item("BF16/M-BF16-00002-of-00002.gguf", 20),
                tree_item("BF16/M-BF16-00001-of-00002.gguf", 30),
                tree_item("M-Q8_0.gguf", 9),
                tree_item("mmproj-M-F16.gguf", 1),
                tree_item("M-imatrix.gguf", 5),
                tree_item("M-iMatrix-Q4_K_M.gguf", 7),
                {"type": "directory", "path": "BF16"},
            ]
        )
        # The imatrix data file is dropped; a model quantised with an imatrix is kept.
        self.assertEqual(
            [f.path for f in files],
            ["BF16/M-BF16-00001-of-00002.gguf", "M-Q8_0.gguf", "M-iMatrix-Q4_K_M.gguf"],
        )
        self.assertEqual(files[0].size, 50)
        self.assertEqual(len(files[0].parts), 2)
        self.assertEqual([p.path for p in mmproj], ["mmproj-M-F16.gguf"])

    def test_header_subset_keeps_only_what_the_launcher_uses(self):
        values = {
            "general.architecture": "qwen35",
            "general.name": "Ornith 1.5 9B",
            "general.description": "x" * 500,
            "general.url": "https://example.com",
            "qwen35.rope.dimension_sections": (11, 11, 10, 0),
            "tokenizer.ggml.model": "gpt2",
            "qwen35.big": ArraySummary(ValueType.INT32, 1000),
            "general.tags": ("gguf", "text-generation"),
            "general.base_model.0.name": "Ornith 1.5 9B",
            "general.base_model.0.license": "mit",
        }
        self.assertEqual(
            header_subset(values),
            {
                "general.architecture": "qwen35",
                "general.name": "Ornith 1.5 9B",
                "qwen35.rope.dimension_sections": [11, 11, 10, 0],
                "general.tags": ["gguf", "text-generation"],
                "general.base_model.0.name": "Ornith 1.5 9B",
            },
        )

    def test_base_models_from_tags(self):
        tags = (
            "base_model:ornith-ai/Ornith-1.5-35B-A3B",
            "base_model:quantized:ornith-ai/Ornith-1.5-35B-A3B",
            "base_model:finetune:deepreinforce-ai/Ornith-1.0-35B",
        )
        self.assertEqual(
            cat._base_models(tags),
            ("ornith-ai/Ornith-1.5-35B-A3B", "deepreinforce-ai/Ornith-1.0-35B"),
        )


class BuilderTest(unittest.TestCase):
    def setUp(self):
        self.hub = FakeHub(
            searches={
                "Ornith": [
                    search_result("ornith-ai/Ornith-1.5-9B-GGUF", tags=("license:mit",)),
                    search_result("x/no-gguf-files") | {"siblings": [{"rfilename": "a.bin"}]},
                    search_result("gated/repo", gated="auto"),
                ],
                "base_model:quantized:ornith-ai/Ornith-1.5-35B-A3B": [
                    search_result(
                        "someone/Finetune-Coder-GGUF",
                        tags=("base_model:quantized:ornith-ai/Ornith-1.5-35B-A3B",),
                    )
                ],
            },
            trees={
                "ornith-ai/Ornith-1.5-9B-GGUF": [
                    tree_item("Ornith-1.5-9B-Q8_0.gguf", 9 * 2**30),
                    tree_item("Ornith-1.5-9B-Q4_K_M.gguf", 5 * 2**30),
                    tree_item("mmproj-Ornith-1.5-9B-BF16.gguf", 2**29),
                ],
                "someone/Finetune-Coder-GGUF": [tree_item("finetune-Q4_K_M.gguf", 20 * 2**30)],
            },
            headers={
                "ornith-ai/Ornith-1.5-9B-GGUF": gguf_bytes(ornith_pairs("9B")),
                "someone/Finetune-Coder-GGUF": gguf_bytes(ornith_pairs("35B")),
            },
        )

    def test_builds_from_search_and_lineage(self):
        built = CatalogBuilder(self.hub, FAMILIES, clock=lambda: 123.0).build()
        self.assertEqual(
            sorted(built.repos),
            ["gated/repo", "ornith-ai/Ornith-1.5-9B-GGUF", "someone/Finetune-Coder-GGUF"],
        )
        self.assertEqual(built.fetched_at, 123.0)
        official = built.repos["ornith-ai/Ornith-1.5-9B-GGUF"]
        self.assertEqual(official.licence, "mit")
        self.assertEqual(official.header_file, "Ornith-1.5-9B-Q4_K_M.gguf")  # smallest file
        self.assertEqual(official.header["qwen35.embedding_length"], 4096)
        self.assertEqual(official.best_mmproj().path, "mmproj-Ornith-1.5-9B-BF16.gguf")
        finetune = built.repos["someone/Finetune-Coder-GGUF"]
        self.assertEqual(finetune.base_models, ("ornith-ai/Ornith-1.5-35B-A3B",))
        gated = built.repos["gated/repo"]
        self.assertTrue(gated.gated)
        self.assertIsNone(gated.header)
        self.assertNotIn("gated/repo", self.hub.tree_calls)

    def test_unchanged_repos_are_reused_and_failures_keep_old_data(self):
        first = CatalogBuilder(self.hub, FAMILIES).build()
        self.hub.tree_calls.clear()
        self.hub.fail.add("someone/Finetune-Coder-GGUF")
        self.hub.searches["base_model:quantized:ornith-ai/Ornith-1.5-35B-A3B"][0]["sha"] = "s2"
        second = CatalogBuilder(self.hub, FAMILIES).build(first)
        self.assertEqual(self.hub.tree_calls, ["someone/Finetune-Coder-GGUF"])
        self.assertEqual(
            second.repos["someone/Finetune-Coder-GGUF"], first.repos["someone/Finetune-Coder-GGUF"]
        )

    def test_reused_repos_get_current_likes_and_downloads(self):
        first = CatalogBuilder(self.hub, FAMILIES).build()
        self.hub.tree_calls.clear()
        for results in self.hub.searches.values():
            for result in results:
                result.update(likes=554, downloads=3860800)
        second = CatalogBuilder(self.hub, FAMILIES).build(first)
        self.assertEqual(self.hub.tree_calls, [])  # nothing read again
        self.assertTrue(second.repos)
        for repo in second.repos.values():
            self.assertEqual((repo.likes, repo.downloads), (554, 3860800))
        again = catalog_from_dict(json.loads(json.dumps(catalog_to_dict(second))), "cache")
        self.assertEqual(again.repos, second.repos)

    def test_saves_progress_periodically(self):
        saved = []
        self.hub.trees["x/no-gguf-files"] = []
        with mock.patch.object(cat, "_has_model_gguf", return_value=True):
            for index in range(25):
                result = search_result(f"bulk/r{index:02d}")
                self.hub.searches["Ornith"].append(result)
                self.hub.trees[result["id"]] = []
            CatalogBuilder(self.hub, FAMILIES).build(save=saved.append)
        self.assertEqual(len(saved), 1)


class CatalogFileTest(unittest.TestCase):
    def test_round_trip(self):
        catalog = official_catalog()
        data = json.loads(json.dumps(catalog_to_dict(catalog)))
        again = catalog_from_dict(data, "cache")
        self.assertEqual(again.repos, catalog.repos)

    def test_newer_cache_wins_over_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "catalog.json"
            seed = Catalog(fetched_at=100, repos={}, source="seed")
            with mock.patch.object(cat, "_load_seed", return_value=seed):
                self.assertEqual(load_catalog(cache).source, "seed")
                save_catalog(Catalog(fetched_at=200, repos={}), cache)
                self.assertEqual(load_catalog(cache).source, "cache")
                save_catalog(Catalog(fetched_at=50, repos={}), cache)
                self.assertEqual(load_catalog(cache).source, "seed")
                cache.write_text("{not json")
                with self.assertLogs(cat.logger, "WARNING"):
                    self.assertEqual(load_catalog(cache).source, "seed")
            with mock.patch.object(cat, "_load_seed", return_value=None):
                cache.unlink()
                self.assertEqual(load_catalog(cache).source, "empty")


class EntriesTest(unittest.TestCase):
    def setUp(self):
        self.entries = {
            (e.repo.id, e.file.name): e for e in entries(official_catalog(), FAMILIES, RULES)
        }

    def test_official_files_are_classified(self):
        entry = self.entries[("ornith-ai/Ornith-1.5-35B-A3B-GGUF", "Ornith-1.5-35B-Q8_0.gguf")]
        self.assertTrue(entry.usable)
        self.assertEqual(entry.classification.family.display_name, "Ornith 1.5 35B-A3B")
        self.assertEqual(entry.classification.quant, "Q8_0")
        self.assertEqual(entry.arch.attention_layers, 10)

    def test_community_and_badges(self):
        low = self.entries[("quant-uploader/Ornith-1.5-397B-GGUF", "Ornith-1.5-397B-IQ1_S.gguf")]
        self.assertEqual(low.classification.source, Source.COMMUNITY)
        self.assertTrue(low.classification.lineage_verified)
        self.assertIn(Badge.LOW_BIT, low.classification.badges)
        self.assertIn(Badge.UNTESTED_LOCALLY, low.classification.badges)
        mystery = self.entries[("someone/Ornith-9B-mystery-GGUF", "Ornith-9B-Q8_0.gguf")]
        self.assertIn(Badge.VERSION_UNKNOWN, mystery.classification.badges)

    def test_partial_files_are_excluded(self):
        catalog = official_catalog()
        repo = catalog.repos["ornith-ai/Ornith-1.5-35B-A3B-GGUF"]
        layer = ModelFile((FilePart("layers/layer-00005.gguf", 500 * 1024**2, None),))
        partial = replace(repo, files=(layer,))
        (entry,) = entries(Catalog(0, {partial.id: partial}), FAMILIES, RULES)
        self.assertFalse(entry.usable)
        self.assertIn("too small for a whole 35B model", entry.classification.excluded)

    def test_draft_heads_are_excluded(self):
        catalog = official_catalog()
        repo = catalog.repos["ornith-ai/Ornith-1.5-9B-GGUF"]
        head = ModelFile((FilePart("mtp-head/mtp-Ornith-9B-head-Q8_0.gguf", 3 * 1024**3, None),))
        small_header = dict(repo.header, **{"general.size_label": "2.3B"})
        draft = ModelFile((FilePart("Ornith-9B-draft-Q8_0.gguf", 3 * 1024**3, None),))
        named = replace(repo, files=(head,))
        declared = replace(repo, files=(draft,), header=small_header, header_file=draft.path)
        for case in (named, declared):
            with self.subTest(case.files[0].path):
                (entry,) = entries(Catalog(0, {case.id: case}), FAMILIES, RULES)
                self.assertFalse(entry.usable)
        self.assertEqual(cat._billions("256x6.7B"), 1715.2)
        self.assertEqual(cat._billions("500M"), 0.5)
        self.assertIsNone(cat._billions("big"))

    def test_single_file_repo_name_gives_the_quant(self):
        catalog = official_catalog()
        repo = catalog.repos["ornith-ai/Ornith-1.5-397B-GGUF"]
        odd = ModelFile((FilePart("model.gguf", 90 * 1024**3, None),))
        renamed = replace(repo, id="someone/Ornith-1.5-397B-TQ1_0", files=(odd,), header_file=None)
        (entry,) = entries(Catalog(0, {renamed.id: renamed}), FAMILIES, RULES)
        self.assertEqual(entry.classification.quant, "TQ1_0")
        self.assertTrue(entry.classification.low_bit)

    def test_gated_repos_are_not_usable(self):
        gated = self.entries[("gated/Ornith-1.5-9B-GGUF", "Ornith-1.5-9B-Q8_0.gguf")]
        self.assertFalse(gated.usable)


if __name__ == "__main__":
    unittest.main()
