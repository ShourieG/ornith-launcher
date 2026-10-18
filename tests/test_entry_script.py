from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import ornith_launcher

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "ornith-launcher"
EXPECTED = f"ornith-launcher {ornith_launcher.__version__} (unofficial)"


@unittest.skipIf(os.name == "nt", "the sh entry script is for Linux and macOS")
class EntryScriptTest(unittest.TestCase):
    def run_script(self, path, *args, env=None):
        return subprocess.run(
            ["/bin/sh", str(path), *args], capture_output=True, text=True, env=env
        )

    def test_prints_version(self):
        result = self.run_script(SCRIPT, "--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), EXPECTED)

    def test_works_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "ornith-launcher"
            link.symlink_to(SCRIPT)
            result = self.run_script(link, "--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), EXPECTED)

    def test_explains_how_to_install_python_when_missing(self):
        # A PATH whose only python3/python report a version below 3.9.
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("python3", "python"):
                fake = Path(tmp) / name
                fake.write_text("#!/bin/sh\nexit 1\n")
                fake.chmod(0o755)
            result = self.run_script(SCRIPT, "--version", env={"PATH": tmp})
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs Python 3.9 or newer", result.stderr)
        self.assertIn("Install it with", result.stderr)


if __name__ == "__main__":
    unittest.main()
