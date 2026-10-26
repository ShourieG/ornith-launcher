from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ornith_launcher import cli
from ornith_launcher.app import App
from ornith_launcher.catalog import catalog as cat
from ornith_launcher.catalog.catalog import save_catalog
from ornith_launcher.config import resolve_paths
from tests.catalog_fixtures import official_catalog
from tests.test_system import spark_host


def make_app(tmp: str) -> App:
    paths = resolve_paths({}, Path(tmp), "linux")
    return App(
        host=spark_host(),
        paths=paths,
        out=io.StringIO(),
        err=io.StringIO(),
        clock=lambda: 1_760_000_000 + 3600,
        os_name="linux",
        machine="aarch64",
    )


class ScanCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = make_app(self.tmp.name)
        save_catalog(official_catalog(), self.app.paths.catalog_file)
        patcher = mock.patch.object(cat, "_load_seed", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *args: str) -> tuple[int, str]:
        code = cli.main(["scan", *args], app=self.app)
        return code, self.app.out.getvalue()

    def test_prints_system_and_suggestions(self):
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("GPU:    NVIDIA GB10 (unified)", out)
        self.assertIn("llama.cpp image: cuda13", out)
        self.assertIn("Catalog: ", out)
        self.assertIn("Suggested for this machine:", out)
        first = out.split("Suggested for this machine:")[1].splitlines()[2]
        self.assertIn("Ornith 1.5 35B-A3B", first)
        self.assertIn("Q8_0", first)
        self.assertIn("262k", first)
        self.assertNotIn("All combinations", out)

    def test_full_list_with_filters(self):
        code, out = self.run_cli("--full", "--size", "397b", "--fits", "--limit", "2")
        self.assertEqual(code, 0)
        listing = out.split("All combinations")[1]
        rows = listing.splitlines()[2:4]
        self.assertTrue(all("397B" in row for row in rows), rows)
        self.assertIn("LOW-BIT", listing)
        self.assertIn("UNTESTED LOCALLY", listing)
        self.assertIn("more. Show more with --limit", listing)

    def test_uncensored_needs_the_flag_and_warns(self):
        _, out = self.run_cli("--full", "--search", "uncensored")
        self.assertIn("All combinations (0,", out)
        self.app.out = io.StringIO()
        _, out = self.run_cli("--full", "--search", "uncensored", "--uncensored")
        self.assertIn("uncensored-Q8_0", out)
        self.assertIn("You alone are responsible", self.app.err.getvalue())

    def test_empty_catalog_says_how_to_fill_it(self):
        self.app.paths.catalog_file.unlink()
        code, out = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("Run: ornith-launcher scan --refresh", out)

    def test_blocking_problem_sets_the_exit_code(self):
        self.app.host = spark_host(programs={"nvidia-smi": "/usr/bin/nvidia-smi"})
        code, out = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("Problem: Docker is not installed.", out)
        self.assertIn("Fix: Install Docker Engine", out)


class PathsTest(unittest.TestCase):
    def test_linux_and_macos_use_xdg(self):
        home = Path("/home/u")
        paths = resolve_paths({}, home, "linux")
        self.assertEqual(paths.config, home / ".config" / "ornith-launcher")
        self.assertEqual(paths.cache, home / ".cache" / "ornith-launcher")
        self.assertEqual(paths.default_models, home / ".ornith-launcher" / "models")
        custom = resolve_paths({"XDG_CONFIG_HOME": "/x", "XDG_CACHE_HOME": "rel"}, home, "darwin")
        self.assertEqual(custom.config, Path("/x/ornith-launcher"))
        self.assertEqual(custom.cache, home / ".cache" / "ornith-launcher")  # relative is ignored

    def test_windows(self):
        home = Path("C:/Users/u")
        paths = resolve_paths(
            {"APPDATA": "C:/Users/u/AppData/Roaming", "LOCALAPPDATA": "C:/Users/u/AppData/Local"},
            home,
            "windows",
        )
        self.assertEqual(paths.config, Path("C:/Users/u/AppData/Roaming/ornith-launcher"))
        self.assertEqual(paths.cache, Path("C:/Users/u/AppData/Local/ornith-launcher/cache"))
        self.assertEqual(paths.default_models, home / ".ornith-launcher" / "models")


if __name__ == "__main__":
    unittest.main()
