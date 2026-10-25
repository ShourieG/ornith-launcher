from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

import ornith_launcher
from ornith_launcher.resources import load_json

ROOT = Path(__file__).resolve().parent.parent
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# Release, pre-release and dev versions as PEP 440 allows them, e.g. 0.1.0, 0.1.0rc1, 0.1.0.dev0.
PEP440 = re.compile(r"\d+\.\d+\.\d+((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?")


class VersionTest(unittest.TestCase):
    def test_version_is_pep440(self):
        self.assertRegex(ornith_launcher.__version__, PEP440)

    def test_module_prints_version(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        result = subprocess.run(
            [sys.executable, "-m", "ornith_launcher", "--version"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        self.assertEqual(
            result.stdout.strip(), f"ornith-launcher {ornith_launcher.__version__} (unofficial)"
        )


class PinsTest(unittest.TestCase):
    def setUp(self):
        self.pins = load_json("pins.json")

    def test_docker_images_are_pinned_by_digest(self):
        build = self.pins["llama_cpp"]["build"]
        flavours = self.pins["docker"]["flavours"]
        self.assertTrue(flavours)
        for name, pin in flavours.items():
            with self.subTest(flavour=name):
                self.assertTrue(pin["tag"].endswith("-" + build), pin["tag"])
                self.assertTrue(pin["digest"].startswith("sha256:"), pin["digest"])
                self.assertRegex(pin["digest"][len("sha256:") :], SHA256_HEX)
                self.assertIn(pin["status"], ("tested", "untested"))

    def test_native_builds_have_size_and_sha256(self):
        native = self.pins["native"]
        build = self.pins["llama_cpp"]["build"]
        self.assertTrue(native["base_url"].endswith(f"/download/{build}/"))
        self.assertTrue(native["builds"])
        for platform, assets in native["builds"].items():
            for asset in assets:
                with self.subTest(platform=platform, asset=asset["name"]):
                    self.assertGreater(asset["size"], 0)
                    self.assertRegex(asset["sha256"], SHA256_HEX)


if __name__ == "__main__":
    unittest.main()
