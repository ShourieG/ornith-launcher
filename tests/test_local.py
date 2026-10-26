from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from ornith_launcher.catalog import local
from ornith_launcher.catalog.catalog import entries
from ornith_launcher.catalog.classify import Source, load_rules
from ornith_launcher.catalog.families import load_families
from ornith_launcher.catalog.gguf import ValueType
from ornith_launcher.catalog.local import (
    Location,
    check_entered_path,
    clean_path_input,
    confirm_checksum,
    identify,
    quick_search,
    search_entered_path,
    search_locations,
)
from tests.catalog_fixtures import official_catalog
from tests.gguf_builder import gguf_bytes, ornith_pairs

FAMILIES = load_families()
RULES = load_rules()
GIB = 1024**3


def fake_model(path: Path, size: str = "35B", name: str = "", length: int = GIB + 4096) -> Path:
    """A sparse file: a real Ornith-shaped header, padded to model size without using disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(gguf_bytes(ornith_pairs(size, name=name)))
        handle.truncate(length)
    return path


def symlink_or_skip(test: unittest.TestCase, link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError:
        test.skipTest("this system does not allow symlinks")


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def locations(self, env=None, extra=()):
        return search_locations(
            env or {}, self.home, "linux", self.home / ".ornith-launcher" / "models", extra
        )

    def test_hugging_face_cache_uses_the_blob_name_as_checksum(self):
        repo = self.home / ".cache/huggingface/hub/models--ornith-ai--Ornith-1.5-35B-A3B-GGUF"
        blob = fake_model(repo / "blobs" / ("ab" * 32))
        snapshot = repo / "snapshots" / "1239361" / "Ornith-1.5-35B-Q8_0.gguf"
        snapshot.parent.mkdir(parents=True)
        symlink_or_skip(self, snapshot, Path("../../blobs") / blob.name)
        (found,) = quick_search(self.locations()).files
        self.assertEqual(found.path, snapshot)
        self.assertEqual(found.real_path, blob.resolve())
        self.assertEqual(found.sha256_hint, "ab" * 32)
        self.assertEqual(found.repo_hint, "ornith-ai/Ornith-1.5-35B-A3B-GGUF")
        self.assertEqual(found.owner_app, "hf-cache")

    def test_ollama_blobs_have_no_extension(self):
        blobs = self.home / "ollama-store" / "blobs"
        fake_model(blobs / ("sha256-" + "cd" * 32), "9B")
        (blobs / ("sha256-" + "ef" * 32)).write_bytes(b"{}")  # a manifest-sized blob
        (found,) = quick_search(self.locations({"OLLAMA_MODELS": str(blobs.parent)})).files
        self.assertEqual(found.owner_app, "ollama")
        self.assertEqual(found.sha256_hint, "cd" * 32)

    def test_lm_studio_and_common_folders(self):
        fake_model(
            self.home / ".lmstudio/models/quant-uploader/Ornith-1.5-9B-GGUF/o-Q8_0.gguf", "9B"
        )
        fake_model(self.home / "models/ornith/Ornith-1.0-35B-Q4_K_M.gguf")
        (self.home / "models/small.gguf").write_bytes(b"GGUF" + b"\0" * 100)  # too small
        notgguf = self.home / "models/fake.gguf"
        with open(notgguf, "wb") as handle:
            handle.truncate(GIB + 1)
        fake_model(self.home / "models/.hidden/x.gguf")
        fake_model(self.home / "models/a/b/c/too-deep.gguf")
        files = quick_search(self.locations()).files
        names = sorted(f.path.name for f in files)
        self.assertEqual(names, ["Ornith-1.0-35B-Q4_K_M.gguf", "o-Q8_0.gguf"])
        studio = next(f for f in files if f.owner_app == "lmstudio")
        self.assertEqual(studio.repo_hint, "quant-uploader/Ornith-1.5-9B-GGUF")

    def test_split_models_need_every_part(self):
        folder = self.home / "models" / "big"
        fake_model(folder / "M-Q4_K_M-00001-of-00003.gguf", "397B", length=4096)
        fake_model(folder / "M-Q4_K_M-00003-of-00003.gguf", "397B", length=4096)
        (found,) = quick_search(self.locations()).files
        self.assertEqual(found.incomplete, "incomplete: part 2 missing")
        fake_model(folder / "M-Q4_K_M-00002-of-00003.gguf", "397B", length=4096)
        (found,) = quick_search(self.locations()).files
        self.assertIsNone(found.incomplete)
        self.assertEqual(len(found.parts), 3)
        self.assertEqual(found.size, 3 * 4096)

    def test_duplicates_reached_twice_are_merged(self):
        target = fake_model(self.home / "models" / "Ornith-1.5-9B-Q8_0.gguf", "9B")
        link = self.home / "Downloads" / "link.gguf"
        link.parent.mkdir()
        symlink_or_skip(self, link, target)
        self.assertEqual(len(quick_search(self.locations()).files), 1)

    def test_entry_limit_skips_a_huge_folder(self):
        folder = self.home / "models"
        folder.mkdir()
        for index in range(30):
            (folder / f"f{index}").touch()
        with mock.patch.object(local, "MAX_ENTRIES", 10):
            result = quick_search(self.locations())
        self.assertEqual(result.skipped[0][0], folder)

    def test_environment_overrides_and_os_rows(self):
        env = {"HF_HOME": "/hf", "OLLAMA_MODELS": "/om", "LLAMA_CACHE": "/lc"}
        paths = {loc.path for loc in self.locations(env, extra=[Path("/data/m")])}
        self.assertTrue({Path("/hf/hub"), Path("/om"), Path("/lc"), Path("/data/m")} <= paths)
        self.assertNotIn(Path("/usr/share/ollama/.ollama/models"), paths)
        mac = search_locations({}, self.home, "darwin", self.home / "m")
        self.assertNotIn(self.home / "Downloads", {loc.path for loc in mac})
        self.assertIn(self.home / "Library/Caches/llama.cpp", {loc.path for loc in mac})


class IdentifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.catalog = entries(official_catalog(), FAMILIES, RULES)

    def found(self, path: Path):
        location = Location(path.parent, None, "tree", "test")
        return quick_search([location]).files[0]

    def test_name_and_size_give_a_likely_catalog_match(self):
        size = next(e.file.size for e in self.catalog if e.file.name == "Ornith-1.5-35B-Q8_0.gguf")
        path = fake_model(self.root / "Ornith-1.5-35B-Q8_0.gguf", length=size)
        model = identify(self.found(path), FAMILIES, RULES, self.catalog)
        self.assertEqual(model.likely.repo.id, "ornith-ai/Ornith-1.5-35B-A3B-GGUF")
        self.assertFalse(model.confirmed)
        self.assertEqual(model.classification.family.display_name, "Ornith 1.5 35B-A3B")
        self.assertEqual(model.classification.source, Source.OFFICIAL)

    def test_unknown_local_file_is_classified_from_its_header(self):
        path = fake_model(self.root / "my-finetune.gguf", "9B", name="Ornith 1.0 9B")
        model = identify(self.found(path), FAMILIES, RULES, self.catalog)
        self.assertIsNone(model.likely)
        self.assertEqual(model.classification.source, Source.LOCAL)
        self.assertEqual(model.classification.version, "1.0")
        self.assertTrue(model.supported)

    def test_unsupported_shape_is_not_supported(self):
        path = self.root / "other.gguf"
        pairs = [p for p in ornith_pairs("9B") if p[0] != "qwen35.embedding_length"]
        pairs.append(("qwen35.embedding_length", ValueType.UINT32, 5120))
        with open(path, "wb") as handle:
            handle.write(gguf_bytes(pairs))
            handle.truncate(GIB + 1)
        model = identify(self.found(path), FAMILIES, RULES, self.catalog)
        self.assertFalse(model.supported)
        self.assertIn("unsupported shape", model.classification.excluded)

    def test_projector_next_to_the_model(self):
        path = fake_model(self.root / "Ornith-1.5-9B-Q8_0.gguf", "9B")
        (self.root / "mmproj-Ornith-1.5-9B-BF16.gguf").write_bytes(b"GGUF")
        model = identify(self.found(path), FAMILIES, RULES, self.catalog)
        self.assertEqual(model.mmproj.name, "mmproj-Ornith-1.5-9B-BF16.gguf")

    def test_checksum_confirmation_is_cached(self):
        path = fake_model(self.root / "Ornith-1.5-9B-Q4_K_M.gguf", "9B", length=4096)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entry = next(e for e in self.catalog if e.file.name == "Ornith-1.5-9B-Q4_K_M.gguf")
        part = replace(entry.file.parts[0], size=4096, sha256=digest)
        small = replace(entry, file=replace(entry.file, parts=(part,)))
        self.assertEqual(search_entered_path(path).files, [])  # below the 1 GiB model minimum
        with mock.patch.object(local, "MIN_MODEL_SIZE", 1):
            item = search_entered_path(path).files[0]
        model = identify(item, FAMILIES, RULES, [small])
        self.assertEqual(model.likely, small)
        cache = self.root / "hashes.json"
        calls = []
        self.assertTrue(confirm_checksum(model, cache, calls.append))
        self.assertTrue(calls)
        calls.clear()
        self.assertTrue(confirm_checksum(model, cache, calls.append))
        self.assertEqual(calls, [])  # the cached digest was used


class PathInputTest(unittest.TestCase):
    def test_cleaning(self):
        home = Path("/home/u")
        env = {"HOME": "/home/u", "MODELS": "/data/models", "USERPROFILE": "C:/Users/u"}
        cases = {
            "'/data/my models/m.gguf' ": "/data/my models/m.gguf",
            '"/data/m.gguf"': "/data/m.gguf",
            "/data/my\\ models/m.gguf": "/data/my models/m.gguf",
            "~/models": "/home/u/models",
            "$MODELS/x.gguf": "/data/models/x.gguf",
            "${MODELS}/x.gguf": "/data/models/x.gguf",
            "file:///data/m.gguf": "/data/m.gguf",
            "$UNSET/x": "$UNSET/x",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_path_input(raw, env, home, posix=True), Path(expected))
        windows = clean_path_input("%USERPROFILE%\\models", env, home, posix=False)
        self.assertEqual(str(windows).replace("\\", "/"), "C:/Users/u/models")

    def test_errors_say_what_is_wrong(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("does not exist", check_entered_path(Path(tmp) / "nope"))
            self.assertIsNone(check_entered_path(Path(tmp)))
            if getattr(os, "geteuid", lambda: 0)() != 0:
                locked = Path(tmp) / "locked"
                locked.mkdir(mode=0)
                try:
                    self.assertIn("cannot be read", check_entered_path(locked))
                finally:
                    locked.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
