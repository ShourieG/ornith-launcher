from __future__ import annotations

import os
import subprocess
import sys
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

    def old_python(self, folder: Path, name: str = "python3") -> None:
        """A Python that fails the 3.9 check and reports 3.8."""
        fake = folder / name
        fake.write_text("#!/bin/sh\ncase $2 in *exit*) exit 1 ;; esac\necho 3.8\n")
        fake.chmod(0o755)

    def test_finds_a_newer_python_installed_alongside(self):
        # Ubuntu 20.04 or RHEL 8: python3 is too old, python3.9 is there too.
        with tempfile.TemporaryDirectory() as tmp:
            self.old_python(Path(tmp))
            (Path(tmp) / "python3.9").symlink_to(sys.executable)
            result = self.run_script(SCRIPT, "--version", env={"PATH": tmp})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), EXPECTED)

    def test_names_the_old_python_it_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.old_python(Path(tmp))
            result = self.run_script(SCRIPT, "--version", env={"PATH": tmp})
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs Python 3.9 or newer; python3 here is 3.8.", result.stderr)
        self.assertIn("sudo apt install python3.9", result.stderr)


if __name__ == "__main__":
    unittest.main()
