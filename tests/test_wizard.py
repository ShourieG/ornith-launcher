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
from ornith_launcher.folders import check_models_folder, cloud_service, hidden_notice, read_mounts
from ornith_launcher.settings import Settings, load_settings, save_settings
from ornith_launcher.ui.prompts import Cancelled, Choice, PlainPrompter
from ornith_launcher.ui.tui import render_list, supports_full_screen
from ornith_launcher.wizard import (
    SetupError,
    Wizard,
    disclaimer_text,
    first_free_port,
)
from tests.catalog_fixtures import official_catalog
from tests.test_local import fake_model
from tests.test_system import spark_host

GIB = 1024**3


class FakeDownloader:
    """Writes sparse files of the right size instead of downloading."""

    def __init__(self, on_progress):
        self.on_progress = on_progress
        self.fetched = []

    def fetch(self, target, stop=None):
        target.dest.parent.mkdir(parents=True, exist_ok=True)
        with open(target.dest, "wb") as handle:
            handle.truncate(target.size)
        self.fetched.append(target)
        return target.dest


class WizardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        host = spark_host()
        host._env["HOME"] = str(self.home)
        self.out = io.StringIO()
        self.app = App(
            host=host,
            paths=resolve_paths({}, self.home, "linux"),
            out=self.out,
            err=io.StringIO(),
            clock=lambda: 1_760_000_000 + 60,
            os_name="linux",
            machine="aarch64",
            full_screen=False,
        )
        save_catalog(official_catalog(), self.app.paths.catalog_file)
        patcher = mock.patch.object(cat, "_load_seed", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.downloaders = []

    def wizard(self, answers, **kwargs) -> Wizard:
        replies = iter(answers)

        def read_line(prompt):
            try:
                return next(replies)
            except StopIteration:
                raise EOFError from None

        def factory(on_progress):
            downloader = FakeDownloader(on_progress)
            self.downloaders.append(downloader)
            return downloader

        kwargs.setdefault("port", 18400)
        return Wizard(
            app=self.app,
            prompter=PlainPrompter(
                read_line=read_line, out=self.out, assume_yes=kwargs.pop("yes", False)
            ),
            settings=load_settings(self.app.paths),
            downloader_factory=factory,
            **kwargs,
        )

    def test_fresh_machine_browses_and_downloads(self):
        answers = [
            "",  # accept the disclaimer
            "b",  # no local models: browse
            "",  # the top suggestion
            "",  # its context
            "",  # default settings
            "",  # keep the models folder
            "",  # go ahead
        ]
        selection = self.wizard(answers).run()
        self.assertEqual(selection.entry.repo.id, "ornith-ai/Ornith-1.5-35B-A3B-GGUF")
        self.assertEqual(selection.candidate.context, 262144)
        self.assertEqual(selection.port, 18400)
        models = self.home / ".ornith-launcher" / "models"
        folder = models / "ornith-ai" / f"Ornith-1.5-35B-A3B-GGUF@{'a' * 7}"
        self.assertEqual(selection.model_paths, [folder / "Ornith-1.5-35B-Q8_0.gguf"])
        self.assertEqual(selection.mmproj_path, folder / "mmproj-BF16.gguf")
        self.assertEqual(len(self.downloaders[0].fetched), 2)
        self.assertEqual(load_settings(self.app.paths).disclaimer_version, disclaimer_text()[0])
        text = self.out.getvalue()
        self.assertIn("not affiliated", text)
        self.assertIn("This folder is hidden", text)
        self.assertIn("No Ornith models found in the usual places.", text)

    def test_local_model_is_used_in_place(self):
        settings = Settings(disclaimer_version=disclaimer_text()[0])
        save_settings(self.app.paths, settings)
        model = fake_model(self.home / "models" / "my-ornith.gguf", "9B", name="Ornith 1.5 9B")
        answers = [
            "1",  # the model found in ~/models
            "y",  # no licence known: I have checked it
            "",  # context
            "",  # settings
            "",  # go ahead
        ]
        selection = self.wizard(answers).run()
        self.assertEqual(selection.local.file.real_path, model.resolve())
        self.assertEqual(selection.model_paths, [model.resolve()])
        self.assertIn("licence_ack", selection.consents)
        self.assertEqual(self.downloaders, [])
        self.assertIn("never moved", self.out.getvalue())
        self.assertIn("will not move or delete it", self.out.getvalue())

    def test_model_path_skips_the_search(self):
        save_settings(self.app.paths, Settings(disclaimer_version=99))
        folder = self.home / "elsewhere"
        fake_model(folder / "Ornith-1.0-9B-Q8_0.gguf", "9B")
        selection = self.wizard(["y", "", "", ""], model_path=folder).run()
        self.assertEqual(selection.entry.classification.version, "1.0")
        self.assertIn(str(folder), load_settings(self.app.paths).extra_model_dirs)

    def test_uncensored_needs_a_typed_confirmation(self):
        save_settings(self.app.paths, Settings(disclaimer_version=99, show_uncensored=True))
        wizard = self.wizard([], yes=True)
        plan = wizard.system_check()
        uncensored = next(s for s in plan.ranked if s.entry.classification.uncensored and s.fits)
        from ornith_launcher.wizard import Selection

        with self.assertRaisesRegex(SetupError, "--yes alone"):
            wizard._consents(Selection(entry=uncensored.entry, candidate=uncensored))
        typed = self.wizard(["I understand"])
        selection = Selection(entry=uncensored.entry, candidate=uncensored)
        self.assertTrue(typed._consents(selection))
        self.assertIn("uncensored_ack", selection.consents)
        wrong = self.wizard(["ok"])
        self.assertFalse(wrong._consents(Selection(entry=uncensored.entry, candidate=uncensored)))

    def test_blocking_problems_stop_setup(self):
        save_settings(self.app.paths, Settings(disclaimer_version=99))
        self.app.host = spark_host(programs={"nvidia-smi": "/usr/bin/nvidia-smi"})
        with self.assertRaisesRegex(SetupError, "cannot continue"):
            self.wizard([]).run()
        self.assertIn("Docker is not installed", self.app.err.getvalue() + self.out.getvalue())

    def test_unusable_folder_asks_for_another(self):
        save_settings(self.app.paths, Settings(disclaimer_version=99))
        (self.home / "usb").mkdir()
        # Resolved: macOS's temporary folders sit behind the /var -> /private/var link.
        usb = (self.home / "usb").resolve()
        self.app.host.files["/proc/mounts"] = f"/dev/sdb1 {usb} vfat rw 0 0\n"
        good = self.home / "big-disk" / "models"
        answers = ["b", "", "", "", "", str(good), "", ""]
        wizard = self.wizard(answers)
        wizard.settings.models_dir = str(self.home / "usb" / "models")
        selection = wizard.run()
        self.assertIn("FAT32", self.out.getvalue())
        self.assertIn("please enter another one", self.out.getvalue())
        self.assertEqual(selection.models_dir, good)
        self.assertEqual(load_settings(self.app.paths).models_dir, str(good))

    def test_cancel_through_the_cli(self):
        self.app.read_line = lambda prompt: "n"
        code = cli.main(["setup", "--plain"], app=self.app)
        self.assertEqual(code, 1)
        self.assertIn("Setup stopped", self.out.getvalue())


class PieceTest(unittest.TestCase):
    def test_settings_round_trip_ignores_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths({}, Path(tmp), "linux")
            settings = Settings(models_dir="/data/m", show_uncensored=True)
            settings.remember_folder(Path("/x"))
            settings.remember_folder(Path("/x"))
            save_settings(paths, settings)
            raw = paths.settings_file.read_text().replace("{", '{"future_key": 1,', 1)
            paths.settings_file.write_text(raw)
            again = load_settings(paths)
            self.assertEqual(again.models_dir, "/data/m")
            self.assertEqual(again.extra_model_dirs, ["/x"])
            self.assertEqual(again.models_path(paths), Path("/data/m"))

    def test_folder_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()  # macOS: /var/folders is a link to /private/var/folders
            mounts = read_mounts(
                f"/dev/a / ext4 rw 0 0\n/dev/b {home}/usb\\040stick exfat rw 0 0\n"
            )
            (home / "usb stick").mkdir()
            ok = check_models_folder(home / "m", GIB, home=home, os_name="linux", mounts=mounts)
            self.assertTrue(ok.ok)
            slow = check_models_folder(
                home / "usb stick" / "m", GIB, home=home, os_name="linux", mounts=mounts
            )
            self.assertTrue(slow.ok)
            self.assertIn("exFAT", slow.warnings[0])
            full = check_models_folder(
                home / "m", GIB, home=home, os_name="linux", disk_free=lambda p: GIB
            )
            self.assertIn("Not enough free space", full.errors[0])
            nowhere = check_models_folder(
                Path("/proc/nonexistent/x"), GIB, home=home, os_name="linux"
            )
            self.assertFalse(nowhere.ok)
        self.assertEqual(cloud_service(Path("/home/u/Dropbox/models")), "Dropbox")
        self.assertEqual(cloud_service(Path("C:/Users/u/OneDrive - Work/m")), "OneDrive")
        self.assertIsNone(cloud_service(Path("/home/u/models")))
        self.assertIn(
            "Ctrl+H", hidden_notice(Path("/h/.ornith-launcher/models"), Path("/h"), "linux")
        )
        self.assertIn("Cmd+Shift+.", hidden_notice(Path("/h/.x"), Path("/h"), "darwin"))
        self.assertIsNone(hidden_notice(Path("/h/models"), Path("/h"), "linux"))
        self.assertIsNone(hidden_notice(Path("C:/h/.x"), Path("C:/h"), "windows"))

    def test_plain_prompter(self):
        out = io.StringIO()
        answers = iter(["9", "x", "2"])
        prompter = PlainPrompter(read_line=lambda p: next(answers), out=out)
        picked = prompter.choose("Pick", ["A"], [Choice(("one",)), Choice(("two",))])
        self.assertEqual(picked, 1)
        self.assertIn("Please enter a number from 1 to 2", out.getvalue())
        keyed = PlainPrompter(read_line=lambda p: "e", out=out)
        self.assertEqual(keyed.choose("Pick", [], [], keys={"e": "enter"}), "e")
        yes = PlainPrompter(read_line=lambda p: "", out=out, assume_yes=True)
        self.assertFalse(yes.confirm("Risky?", default=False))
        self.assertFalse(yes.typed_confirmation("warning", "I understand"))
        with self.assertRaises(Cancelled):
            PlainPrompter(read_line=lambda p: "q", out=out).choose("x", ["A"], [Choice(("a",))])

    def test_full_screen_rendering(self):
        choices = [
            Choice(("first", "1")),
            Choice(("second", "2"), detail="more"),
            Choice(("off",), enabled=False),
        ]
        lines = render_list("Title", ["Name", "N"], choices, 1, 0, {"a": "all"}, 60, 20)
        self.assertIn("Title", lines[0])
        self.assertTrue(lines[3].startswith("\x1b[7m> second"))
        self.assertIn("more", lines[-2])
        self.assertIn("a all", lines[-1])
        self.assertFalse(supports_full_screen(io.StringIO(), io.StringIO(), {"TERM": "xterm"}))

    def test_in_app_disclaimer_matches_disclaimer_md(self):
        version, text = disclaimer_text()
        root = Path(__file__).resolve().parent.parent
        self.assertIn(f"Version {version} ", (root / "DISCLAIMER.md").read_text(encoding="utf-8"))
        self.assertIn("unofficial", text)
        self.assertIn("not supported by Anthropic", text)

    def test_first_free_port_skips_busy_ports(self):
        import socket

        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            port = busy.getsockname()[1]
            self.assertNotEqual(first_free_port(port, 5), port)


if __name__ == "__main__":
    unittest.main()
