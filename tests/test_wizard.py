from __future__ import annotations

import io
import os
import re
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
from ornith_launcher.system.memory import Pool, PoolKind
from ornith_launcher.ui.prompts import Cancelled, Choice, PlainPrompter
from ornith_launcher.ui.tui import click_hint, render_list, supports_full_screen
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
            "d",  # no local models: browse and download
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
        models = self.home / "models" / "ornith-launcher"
        folder = models / "ornith-ai" / f"Ornith-1.5-35B-A3B-GGUF@{'a' * 7}"
        self.assertEqual(selection.model_paths, [folder / "Ornith-1.5-35B-Q8_0.gguf"])
        self.assertEqual(selection.mmproj_path, folder / "mmproj-BF16.gguf")
        self.assertEqual(len(self.downloaders[0].fetched), 2)
        from ornith_launcher.installed import load_installed

        recorded = load_installed(self.app.paths).downloads
        self.assertEqual(recorded, [str(t.dest) for t in self.downloaders[0].fetched])
        self.assertEqual(load_settings(self.app.paths).disclaimer_version, disclaimer_text()[0])
        text = self.out.getvalue()
        self.assertIn("not affiliated", text)
        self.assertNotIn("This folder is hidden", text)  # the default folder is visible
        self.assertIn("No Ornith models found in the usual places.", text)
        # Each file says what it is for; the vision file can be left out in the settings.
        self.assertRegex(text, r"Vision\s+on: adds a 0\.8 GiB vision file so the model")
        self.assertIn("(model 35.2 GiB + vision file 0.8 GiB, about", text)
        self.assertIn(f"Downloading 2 files, 36.0 GiB in all, into {folder}\n", text)
        self.assertIn(
            "Ctrl+C pauses the download; then keep it to resume later, or delete it.", text
        )
        self.assertIn("\n1/2  Model: Ornith-1.5-35B-Q8_0.gguf, 35.2 GiB\n", text)
        self.assertIn(
            "\n2/2  Vision file (lets the model read images): mmproj-BF16.gguf, 0.8 GiB\n", text
        )
        self.assertEqual(text.count("     Done, sha256 checked."), 2)

    def pausing(self):
        """A downloader factory whose first fetch stops a quarter of the way, like Ctrl+C."""
        import json

        def factory(on_progress):
            class Pausing(FakeDownloader):
                def fetch(self, target, stop=None):
                    state = {"size": 400, "segments": [[0, 400, 100]]}
                    target.dest.parent.mkdir(parents=True, exist_ok=True)
                    Path(str(target.dest) + ".part").write_bytes(b"")
                    Path(str(target.dest) + ".part.json").write_text(json.dumps(state))
                    raise KeyboardInterrupt

            downloader = Pausing(on_progress)
            self.downloaders.append(downloader)
            return downloader

        return factory

    def test_ctrl_c_pauses_the_download_and_keeps_it_by_default(self):
        from ornith_launcher.download import DownloadPaused

        wizard = self.wizard(["", "d", "", "", "", "", "", ""])  # ... go ahead, then keep it
        wizard.downloader_factory = self.pausing()
        with self.assertRaises(DownloadPaused) as caught:
            wizard.run()
        self.assertIn(
            "is paused at 25% (0.0 GiB of 0.0 GiB). Nothing is lost", str(caught.exception)
        )
        self.assertIn("run ornith-launcher setup and press r", caught.exception.hint)
        self.assertIn("choose Remove unfinished downloads", caught.exception.hint)
        text = self.out.getvalue()
        self.assertIn("Download paused at 25%", text)
        self.assertIn("Resume later", text)
        self.assertIn("Delete it and exit", text)
        from ornith_launcher.unfinished import unfinished_downloads

        self.assertEqual(len(unfinished_downloads(self.app.paths)), 1)

    def test_deleting_a_paused_download_removes_its_files_and_record(self):
        from ornith_launcher.installed import load_installed
        from ornith_launcher.unfinished import choices_file

        wizard = self.wizard(["", "d", "", "", "", "", "", "2"])  # ... go ahead, then delete
        wizard.downloader_factory = self.pausing()
        with self.assertRaises(Cancelled):
            wizard.run()
        folder = self.home / "models" / "ornith-launcher" / "ornith-ai"
        self.assertFalse(folder.exists())  # the files and their empty folders
        self.assertEqual(load_installed(self.app.paths).downloads, [])
        model = folder / f"Ornith-1.5-35B-A3B-GGUF@{'a' * 7}"
        self.assertFalse(choices_file(self.app.paths, model).exists())
        self.assertIn("Deleted the download", self.out.getvalue())

    def test_resuming_goes_on_with_the_settings_chosen_before(self):
        from ornith_launcher.download import DownloadPaused
        from ornith_launcher.unfinished import choices_file

        answers = [
            "",  # accept the disclaimer
            "d",  # browse and download
            "",  # the top suggestion
            "3",  # 128k context
            "3",  # settings: parallel requests
            "2",  # 1 slot
            "5",  # port
            "18450",
            "6",  # continue with these settings
            "",  # keep the models folder
            "",  # go ahead
            "",  # paused: keep it
        ]
        wizard = self.wizard(answers, port=None)
        wizard.downloader_factory = self.pausing()
        with self.assertRaises(DownloadPaused):
            wizard.run()
        self.out.truncate(0)
        self.out.seek(0)
        selection = self.wizard(["r", ""], port=None).run()  # resume, the stopped download
        self.assertEqual(
            (selection.candidate.context, selection.candidate.slots, selection.port),
            (131072, 1, 18450),
        )
        text = self.out.getvalue()
        self.assertNotIn("Context size", text)  # nothing is asked again
        self.assertNotIn("Go ahead?", text)
        self.assertIn("Resuming the download", text)
        self.assertIn("With the settings chosen when it started:", text)
        self.assertIn("context 128k, KV f16, 1 slot, vision on, port 18450", text)
        self.assertEqual(len(self.downloaders[-1].fetched), 2)
        # The saved settings stay until setup saves the profile (run_setup forgets them).
        self.assertTrue(choices_file(self.app.paths, selection.model_paths[0].parent).exists())

    def test_refusing_a_confirmation_on_resume_shows_the_list_again(self):
        from ornith_launcher.unfinished import save_choices

        save_settings(self.app.paths, Settings(disclaimer_version=disclaimer_text()[0]))
        folder = self.home / "models" / "ornith-launcher" / "ornith-ai"
        folder = folder / f"Ornith-1.5-35B-A3B-GGUF@{'a' * 7}"
        self.stopped_download(folder, "Ornith-1.5-35B-Q8_0.gguf")
        earlier = {"file": "Ornith-1.5-35B-Q8_0.gguf", "context": 262144, "kv_type": "f16"}
        earlier.update(slots=4, vision=True, placement="single", port=18400, consents={})
        save_choices(self.app.paths, folder, earlier)
        with mock.patch.object(Wizard, "_consents_given", return_value=False):
            with mock.patch.object(Wizard, "_consents", side_effect=[False, True]):
                selection = self.wizard(["r", "", ""]).run()  # refused once, then accepted
        self.assertTrue(selection.preset)
        self.assertEqual(self.out.getvalue().count("Unfinished downloads (pick one"), 2)

    def test_a_finished_setup_forgets_the_saved_download_settings(self):
        from ornith_launcher import setup_profile
        from ornith_launcher import wizard as wizard_module
        from ornith_launcher.unfinished import choices_file, save_choices

        folder = self.home / "models" / "ornith-ai" / "Ornith-1.5-9B-GGUF@abc1234"
        save_choices(self.app.paths, folder, {"file": "m.gguf"})
        selection = mock.Mock(needs_download=True, model_paths=[folder / "m.gguf"])
        with mock.patch.object(wizard_module.Wizard, "run", return_value=selection):
            with mock.patch.object(wizard_module, "finish"):
                with mock.patch.object(setup_profile, "finish_setup"):
                    with mock.patch.object(setup_profile, "launch_now", return_value=0):
                        self.assertEqual(cli.main(["setup", "--plain"], app=self.app), 0)
        self.assertFalse(choices_file(self.app.paths, folder).exists())

    def test_vision_off_leaves_the_vision_file_out(self):
        answers = [
            "",  # accept the disclaimer
            "d",  # browse and download
            "",  # the top suggestion
            "",  # its context
            "4",  # settings: vision
            "2",  # off
            "6",  # continue with these settings
            "",  # keep the models folder
            "",  # go ahead
        ]
        wizard = self.wizard(answers)
        prompts = []
        read_line = wizard.prompter.read_line
        wizard.prompter.read_line = lambda prompt: prompts.append(prompt) or read_line(prompt)
        selection = wizard.run()
        text = self.out.getvalue()
        self.assertRegex(
            text, r"on\s+46\.7 GiB\s+yes\s+69\.8 GiB\s+reads images; adds a 0\.8 GiB vision file"
        )
        self.assertRegex(text, r"off\s+45\.8 GiB\s+yes\s+70\.6 GiB\s+text only")
        self.assertIn("Settings for Ornith 1.5 35B-A3B Q8_0:", text)
        self.assertIn("Downloading 1 file, 35.2 GiB in all", text)
        self.assertNotIn("Vision file", text)
        self.assertIsNone(selection.mmproj_path)
        self.assertEqual(len(self.downloaders[0].fetched), 1)

    def stopped_download(self, folder: Path, name: str, done: int = 100, size: int = 400) -> Path:
        """A download stopped part way, as the downloader and the install record leave it."""
        import json

        from ornith_launcher.installed import record

        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / name
        Path(str(dest) + ".part").write_bytes(b"\0" * 16)
        state = {"url": "u", "size": size, "sha256": None, "segments": [[0, size, done]]}
        Path(str(dest) + ".part.json").write_text(json.dumps(state))
        record(self.app.paths, "downloads", str(dest))
        return dest

    def test_a_stopped_download_is_shown_and_resumed_in_its_own_folder(self):
        settings = Settings(
            disclaimer_version=disclaimer_text()[0], models_dir=str(self.home / "new")
        )
        save_settings(self.app.paths, settings)
        folder = self.home / "old" / "ornith-ai" / f"Ornith-1.5-35B-A3B-GGUF@{'a' * 7}"
        dest = self.stopped_download(folder, "Ornith-1.5-35B-Q8_0.gguf")
        # resume, the stopped download, its context, default settings, go ahead
        selection = self.wizard(["r", "", "", "", ""]).run()
        text = self.out.getvalue()
        self.assertIn("A download was stopped: Ornith-1.5-35B-Q8_0.gguf (25% done).", text)
        self.assertIn("Unfinished downloads (pick one to resume where it stopped):", text)
        self.assertNotIn("\nModels folder\n", text)  # the files stay where they are
        self.assertIn(f"resumes in {folder}", text)
        self.assertIn("     Resuming: 0.0 GiB is already here.", text)
        self.assertEqual(selection.models_dir, self.home / "old")
        self.assertEqual(self.downloaders[0].fetched[0].dest, dest)

    def test_quitting_setup_says_a_stopped_download_is_kept(self):
        save_settings(self.app.paths, Settings(disclaimer_version=disclaimer_text()[0]))
        models = self.home / "models" / "ornith-launcher"
        part = self.stopped_download(models / "ornith-ai" / "Ornith-1.5-9B-GGUF@abc1234", "m.gguf")
        self.app.read_line = lambda prompt: "q"
        self.assertEqual(cli.main(["setup", "--plain"], app=self.app), 1)
        self.assertIn("A stopped download is kept: resume it with r", self.out.getvalue())
        self.assertTrue(Path(str(part) + ".part").exists())

    def test_a_stopped_download_no_longer_listed_says_how_to_remove_it(self):
        save_settings(self.app.paths, Settings(disclaimer_version=disclaimer_text()[0]))
        models = self.home / "models" / "ornith-launcher"
        self.stopped_download(models / "gone-uploader" / "Removed-Repo-GGUF@1234567", "x.gguf")
        with self.assertRaises(Cancelled):
            self.wizard(["r", "1", "esc", "q"]).run()
        text = self.out.getvalue()
        self.assertIn("is no longer in the model list", text)
        self.assertIn("Remove it with u, Uninstall.", text)
        self.assertIn("That option is not available here.", text)

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

    def test_rows_link_to_model_cards(self):
        shown = []
        original = PlainPrompter.choose

        def record(prompter, title, headers, choices, **kwargs):
            shown.append(choices)
            return original(prompter, title, headers, choices, **kwargs)

        with mock.patch.object(PlainPrompter, "choose", record):
            self.wizard(["", "d", "", "", "", "", ""]).run()
        suggested = shown[1]
        self.assertEqual(
            suggested[0].link, "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF"
        )
        self.app.host._env["HF_ENDPOINT"] = "https://hf-mirror.example/"
        self.assertEqual(self.wizard([])._card("a/b"), "https://hf-mirror.example/a/b")

    def test_only_uncensored_asks_first_and_warns(self):
        shown = []
        original = PlainPrompter.choose

        def record(prompter, title, headers, choices, **kwargs):
            shown.append((title, kwargs.get("notice", ""), choices))
            return original(prompter, title, headers, choices, **kwargs)

        with mock.patch.object(PlainPrompter, "choose", record):
            with self.assertRaises(Cancelled):
                # disclaimer, browse, all models, filters, uncensored, only: decline, then
                # again and accept, back to the list, quit
                answers = ["", "d", "a", "f", "11", "3", "n", "11", "3", "y", "esc", "q"]
                self.wizard(answers).run()
        titles = [title for title, _, _ in shown]
        self.assertEqual(titles.count("Uncensored models:"), 2)
        self.assertTrue(titles[-2].startswith("Filters ("))
        self.assertEqual(shown[-2][2][10].cells, ("Uncensored", "only uncensored"))
        title, notice, choices = shown[-1]
        self.assertTrue(title.startswith("All models ("))
        self.assertIn("Filters: only uncensored.", notice)
        self.assertIn("You alone are responsible", notice)
        self.assertIn("can miss some or include others", notice)
        self.assertTrue(choices)
        plan = self.wizard([]).system_check()
        uncensored = {c.entry.repo.id for c in plan.ranked if c.entry.classification.uncensored}
        self.assertTrue(all(c.link.split("huggingface.co/")[1] in uncensored for c in choices))
        self.assertTrue(load_settings(self.app.paths).show_uncensored)
        self.assertIn("in later runs too, until you hide them again", self.out.getvalue())

    def test_lists_say_when_uncensored_models_are_shown(self):
        save_settings(self.app.paths, Settings(disclaimer_version=99, show_uncensored=True))
        titles, _ = self.walk(["d", "a", "q"])
        self.assertIn("Suggested for this machine (uncensored shown):", titles)
        self.assertIn("Filters: uncensored shown. Search: none.", self.out.getvalue())

    def walk(self, answers, **kwargs):
        """Run the wizard; returns (titles of every menu shown, selection or None if quit)."""
        titles = []
        original = PlainPrompter.choose

        def record(prompter, title, headers, choices, **kw):
            titles.append(title)
            return original(prompter, title, headers, choices, **kw)

        with mock.patch.object(PlainPrompter, "choose", record):
            try:
                return titles, self.wizard(answers, **kwargs).run()
            except Cancelled:
                return titles, None

    def test_back_from_the_context_step_returns_to_the_same_list(self):
        # disclaimer, browse, best fit list, sort, by likes, first row, back, first row again
        titles, selection = self.walk(["", "d", "b", "s", "9", "1", "esc", "1", "", "", "", ""])
        self.assertIsNotNone(selection)
        context = "Context size (the longest prompt the model can read at once):"
        index = titles.index(context)
        self.assertTrue(titles[index - 1].startswith("Best fit for this machine ("))
        self.assertIn("likes, most first", titles[index + 1])  # same list, same order
        self.assertEqual(titles[index + 2], context)

    def test_main_menu_and_back_lead_to_the_first_menu(self):
        # browse, best fit list, first row, main menu, browse, back (to the first menu), quit
        titles, _ = self.walk(["", "d", "b", "1", "m", "d", "esc", "q"])
        self.assertEqual(titles.count("What next?"), 3)
        self.assertEqual(titles[-1], "What next?")
        after_main = titles[titles.index("What next?", 1) + 1]
        self.assertEqual(after_main, "Suggested for this machine:")  # browsing starts afresh
        # The quick search ran once; coming back shows its result again on a fresh screen.
        self.assertEqual(self.out.getvalue().count("Looking for Ornith models"), 1)
        self.assertEqual(self.out.getvalue().count("No Ornith models found"), 3)

    def test_coming_back_to_the_first_menu_starts_a_new_screen(self):
        screens = []
        original = PlainPrompter.new_screen

        def record(prompter, title):
            screens.append(title)
            original(prompter, title)

        with mock.patch.object(PlainPrompter, "new_screen", record):
            # browse, back from the suggestions, browse, back again, quit
            self.walk(["", "d", "esc", "d", "esc", "q"])
        # The first menu sits under the system check; each return clears the screen
        # instead of printing a second menu below the first.
        self.assertEqual(
            screens,
            ["Checking this machine...", "Models on this machine", "Models on this machine"],
        )

    def test_back_from_a_local_model_shows_the_first_menu(self):
        settings = Settings(disclaimer_version=disclaimer_text()[0])
        save_settings(self.app.paths, settings)
        fake_model(self.home / "models" / "my-ornith.gguf", "9B", name="Ornith 1.5 9B")
        titles, _ = self.walk(["1", "y", "esc", "q"])
        found = "Ornith models found on this machine (used where they are, never moved):"
        self.assertEqual(titles, [found, titles[1], found])

    def test_model_path_is_used_once(self):
        save_settings(self.app.paths, Settings(disclaimer_version=disclaimer_text()[0]))
        folder = self.home / "elsewhere"
        fake_model(folder / "Ornith-1.0-9B-Q8_0.gguf", "9B")
        titles, _ = self.walk(["y", "\x1b", "q"], model_path=folder)  # Esc typed into the line
        # Back shows the first menu (which now lists the remembered folder's model),
        # not the same model's context step again.
        self.assertEqual(len(titles), 2)
        self.assertTrue(titles[0].startswith("Context size"))
        self.assertTrue(titles[1].startswith("Ornith models found on this machine"))

    def test_each_step_starts_a_new_screen(self):
        screens = []
        original = PlainPrompter.new_screen

        def record(prompter, title):
            screens.append(title)
            original(prompter, title)

        with mock.patch.object(PlainPrompter, "new_screen", record):
            self.wizard(["", "d", "", "", "", "", ""]).run()
        self.assertEqual(screens, ["Checking this machine...", "Models folder", "Summary"])

    def test_uninstall_is_offered_only_when_the_launcher_installed_something(self):
        titles, _ = self.walk(["", "q"])
        self.assertNotIn("uninstall", self.out.getvalue().lower())
        from ornith_launcher.installed import record

        record(self.app.paths, "shortcuts", str(self.home / "Desktop" / "x.desktop"))
        self.out.truncate(0)
        # first menu, uninstall, back from the uninstall menu, first menu again, quit
        titles, _ = self.walk(["u", "esc", "q"])
        self.assertIn("Uninstall or remove setups", self.out.getvalue())
        self.assertEqual(titles, ["What next?", "Uninstall", "What next?"])

    def test_after_a_removal_the_menu_starts_a_new_screen(self):
        from ornith_launcher.profiles import save_profile
        from tests.runtime_fixtures import profile

        self.walk(["", "q"])
        save_profile(self.app.paths, profile(self.home, name="nine"))
        paused = []
        original = PlainPrompter.pause

        def pause(prompter, message):
            paused.append(message)
            return original(prompter, message)

        with mock.patch.object(PlainPrompter, "pause", pause):
            # uninstall, one setup, "nine", setup only, confirm, Enter, then quit
            self.walk(["u", "1", "1", "", "y", "", "q"])
        self.assertEqual(paused, ["Press Enter to go back to the menu."])
        text = self.out.getvalue()
        after = text[text.index("  Removed the setup nine.") :]
        self.assertIn("\nModels on this machine\n", after)

    def test_launch_a_model_from_the_first_menu(self):
        from ornith_launcher import launch
        from ornith_launcher.profiles import save_profile
        from tests.runtime_fixtures import profile

        self.walk(["", "q"])
        self.assertNotIn("Launch a model", self.out.getvalue())  # nothing to launch yet
        broken = profile(self.home, name="broken")
        broken.model.parts = [str(self.home / "gone.gguf")]
        save_profile(self.app.paths, broken)
        save_profile(self.app.paths, profile(self.home, name="ready"))
        save_settings(self.app.paths, Settings(disclaimer_version=99, default_profile="ready"))
        # launch list, broken (not available), back to the first menu, launch list, ready
        answers = iter(["l", "1", "esc", "l", "2"])

        def read_line(prompt):
            try:
                return next(answers)
            except StopIteration:
                raise EOFError from None

        self.app.read_line = read_line
        with mock.patch.object(
            launch,
            "preflight",
            side_effect=lambda app, p, rt, **kw: (
                ["The model file is missing. Repair it with: ornith-launcher setup --repair broken"]
                if p.name == "broken"
                else []
            ),
        ):
            with mock.patch.object(launch, "run_launch", return_value=0) as run:
                self.assertEqual(cli.main(["setup", "--plain"], app=self.app), 0)
        self.assertEqual(run.call_args.args[1].name, "ready")
        text = self.out.getvalue()
        self.assertIn("That option is not available here.", text)
        self.assertIn("setup --repair broken", text)
        self.assertEqual(text.count("Launch a model:"), 2)  # Back really went back

    def test_custom_model_path_key(self):
        save_settings(self.app.paths, Settings(disclaimer_version=disclaimer_text()[0]))
        folder = self.home / "elsewhere"
        model = fake_model(folder / "Ornith-1.0-9B-Q8_0.gguf", "9B")
        # An empty answer asks again and b goes back to the menu, then the path is given.
        answers = ["p", "", "esc", "p", str(folder), "y", "", "", ""]
        selection = self.wizard(answers).run()
        self.assertEqual(selection.model_paths, [model.resolve()])
        self.assertRegex(self.out.getvalue(), r"Custom model path +p\n")

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
        # A wrong answer or a bare Enter asks again; only Esc goes back.
        wrong = self.wizard(["ok", "", "b", "\x1b"])
        self.assertFalse(wrong._consents(Selection(entry=uncensored.entry, candidate=uncensored)))
        self.assertEqual(self.out.getvalue().count("(quotes are optional) to continue, or Esc"), 3)
        for typed_text in ('"I understand"', "\u201ci understand\u201d", "  I   UNDERSTAND "):
            quoted = self.wizard([typed_text])
            self.assertTrue(
                quoted._consents(Selection(entry=uncensored.entry, candidate=uncensored))
            )
        from ornith_launcher.ui.prompts import same_words

        self.assertFalse(same_words("I understand it", "I understand"))
        self.assertFalse(same_words("understand", "I understand"))

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
        answers = ["d", "", "", "", "", str(good), "", ""]
        wizard = self.wizard(answers)
        wizard.settings.models_dir = str(self.home / "usb" / "models")
        selection = wizard.run()
        self.assertIn("FAT32", self.out.getvalue())
        self.assertIn("please enter another one", self.out.getvalue())
        self.assertEqual(selection.models_dir, good)
        self.assertEqual(load_settings(self.app.paths).models_dir, str(good))

    def test_the_context_screen_shows_memory_and_asks_before_a_stretch(self):
        answers = [
            "",  # accept the disclaimer
            "d",  # browse and download
            "3",  # the 122B suggestion
            "6",  # 1M context: a stretch on this machine
            "n",  # not that one
            "",  # the recommended one
            "",  # default settings
            "",  # keep the models folder
            "",  # go ahead
        ]
        wizard = self.wizard(answers)
        prompts = []
        read_line = wizard.prompter.read_line
        wizard.prompter.read_line = lambda prompt: prompts.append(prompt) or read_line(prompt)
        selection = wizard.run()
        self.assertEqual(selection.entry.repo.id, "ornith-ai/Ornith-1.5-122B-GGUF")
        self.assertEqual(selection.candidate.context, 262144)
        text = self.out.getvalue()
        self.assertIn("so 116.5 GiB is usable", text)
        self.assertIn("Setup plans up to 99.0 GiB and keeps 17.5 GiB (15%) in reserve", text)
        self.assertIn("The model weights take 70.6 GiB, plus 0.8 GiB for the vision file;", text)
        self.assertIn("\nPlan left: room before the reserve.", text)  # the key on its own line
        self.assertIn("All memory figures here are ESTIMATED.", text)
        header = "Context memory  Total      Plan left  Reserve used     Free after  Verdict\n"
        self.assertIn(header, text)  # no Tested column when every row says the same
        self.assertRegex(
            text,
            r"262k \(native\)\s+12\.2 GiB\s+83\.7 GiB\s+15\.3 GiB\s+none\s+32\.8 GiB\s+recommended",
        )
        self.assertRegex(
            text,
            r"1M\s+33\.8 GiB\s+105\.2 GiB\s+0\.0 GiB\s+6\.2 of 17\.5 GiB\s+11\.2 GiB\s+stretch",
        )
        self.assertIn("A stretch for this machine", text)
        self.assertIn(
            "Warning: Ornith 1.5 122B Q4_K_M at 1M context needs 105.2 GiB, 6.2 GiB", text
        )
        asked = [p for p in prompts if "Use it anyway?" in p]
        self.assertEqual(asked, ["Use it anyway? [y/N] (default: no) "])
        self.assertEqual(text.count("Context size (the longest prompt"), 2)  # shown again

    def test_the_context_screen_keeps_the_tested_column_when_rows_differ(self):
        wizard = self.wizard(["", "d", "1"])  # the 35B Q8_0, tested at 1M on this machine
        with self.assertRaises(Cancelled):
            wizard.run()
        text = self.out.getvalue()
        self.assertRegex(text, r"Free after\s+Verdict\s+Tested\n")
        self.assertRegex(text, r"1M\s.*fits\s+TESTED")
        self.assertNotIn("All memory figures here are", text)

    def test_too_big_is_shown_and_can_be_chosen_at_your_own_risk(self):
        from ornith_launcher.browse import recommended
        from ornith_launcher.planner.rank import Filters, Fit

        wizard = self.wizard(["", "y"])  # no at first, then yes
        plan = wizard.system_check()
        too_big = [c for c in recommended(plan.ranked).values() if c.fit is Fit.TOO_BIG]
        self.assertTrue(too_big)
        big = too_big[0]
        # A pick starts from the file's smallest setting, so the verdict is its best case.
        listed = Filters(show_uncensored=False)  # the list's own filters
        same_file = [
            c
            for c in plan.ranked
            if c.entry.file.path == big.entry.file.path and listed.accepts(c) and c.vision
        ]
        self.assertTrue(big.vision)  # as the ranking prefers it, so the next screens match
        self.assertEqual(big.estimate.total, min(c.estimate.total for c in same_file))
        self.assertIn("too big", wizard._cells(big))
        selection = mock.Mock(accepted_fit=Fit.FITS)
        self.assertFalse(wizard._accept_fit(selection, big))  # Enter: no
        self.assertTrue(wizard._accept_fit(selection, big))
        self.assertIs(selection.accepted_fit, Fit.TOO_BIG)
        self.assertTrue(wizard._accept_fit(selection, big))  # accepted once, not asked again
        text = self.out.getvalue()
        self.assertEqual(text.count("Too big for this machine"), 2)
        self.assertIn("at your own risk", text)
        self.assertIn("most likely fail to load", text)

    def test_picking_a_too_big_file_in_all_models_warns_first(self):
        # disclaimer, browse, all models, the 397B Q8_0 at the top: decline, then quit
        wizard = self.wizard(["", "d", "a", "1", "", "q"])
        prompts = []
        read_line = wizard.prompter.read_line
        wizard.prompter.read_line = lambda prompt: prompts.append(prompt) or read_line(prompt)
        with self.assertRaises(Cancelled):
            wizard.run()
        text = self.out.getvalue()
        self.assertIn("Too big for this machine", text)
        self.assertIn("Warning: Ornith 1.5 397B Q8_0 at 32k context needs", text)
        self.assertIn("Download it anyway, at your own risk? [y/N] (default: no) ", prompts)
        self.assertTrue(text.rstrip().endswith("Quit                      q"))  # same list again
        self.assertEqual(text.count("All models (21 files;"), 2)

    def test_a_pick_in_all_models_starts_from_the_recommended_setting(self):
        # disclaimer, browse, all models, row 8 (the 1.5 35B-A3B Q8_0, shown at 32k), then
        # Enter on the context screen and the defaults after it
        titles, selection = self.walk(["", "d", "a", "8", "", "", "", ""])
        text = self.out.getvalue()
        self.assertRegex(text, r"8\s+Ornith 1\.5 35B-A3B\s+official\s+mit\s+Q8_0\s+40\.0 GiB")
        self.assertIn("Context size (the longest prompt the model can read at once):", titles)
        # Enter takes the recommended setting (262k, vision on), not the row's 32k.
        self.assertEqual(selection.candidate.context, 262144)
        self.assertTrue(selection.candidate.vision)

    def test_quitting_clears_the_full_screen_then_says_what_was_kept(self):
        from contextlib import nullcontext
        from dataclasses import replace

        from ornith_launcher.ui import tui
        from ornith_launcher.ui.tui import CLEAR

        save_settings(self.app.paths, Settings(disclaimer_version=99))
        for full, cleared in ((True, True), (False, False)):
            out = io.StringIO()
            app = replace(self.app, out=out, full_screen=full, read_line=lambda prompt: "q")
            # The full screen reads one key at a time; q quits from the first menu.
            with mock.patch.object(tui, "read_key", lambda timeout=None: "q"):
                with mock.patch.object(tui, "raw_keys", nullcontext):
                    self.assertEqual(cli.main(["setup"], app=app), 1)
            text = out.getvalue()
            self.assertIn("Setup stopped. Nothing else was changed;", text)
            self.assertEqual(CLEAR in text, cleared)
            if cleared:  # the message comes after the clear, on an empty screen
                self.assertLess(text.rindex(CLEAR), text.index("Setup stopped."))

    def test_a_quit_no_command_handles_ends_cleanly(self):
        from dataclasses import replace

        from ornith_launcher.ui.prompts import Cancelled

        out = io.StringIO()
        app = replace(self.app, out=out)
        with mock.patch.object(cli.scan, "run", side_effect=Cancelled()):
            self.assertEqual(cli.main(["scan"], app=app), 1)
        self.assertEqual(out.getvalue(), "\nStopped. Nothing was changed.\n")

    def test_esc_before_the_download_goes_back_one_step_at_a_time(self):
        # suggestion, context, continue; folder: keep; summary: Esc (to the folder), Esc (to
        # the settings, not the context screen), continue; keep the folder; go ahead
        answers = ["", "d", "1", "", "", "", "esc", "esc", "", "", ""]
        titles, selection = self.walk(answers)
        self.assertIsNotNone(selection)
        text = self.out.getvalue()
        self.assertEqual(text.count("\nModels folder\n"), 3)
        self.assertEqual(text.count("\nSummary\n"), 2)
        context = "Context size (the longest prompt the model can read at once):"
        self.assertEqual(titles.count(context), 1)  # Esc led to the settings menu itself
        self.assertEqual(len([t for t in titles if t.startswith("Settings for ")]), 2)
        prompts = []
        PlainPrompter(read_line=lambda p: prompts.append(p) or "", out=io.StringIO()).confirm(
            "Go ahead?", back=True
        )
        self.assertEqual(prompts, ["Go ahead? [Y/n] (default: yes; Esc to go back) "])

    def test_esc_keeps_the_search(self):
        # all models, search 397B, search again but Esc, quit
        titles, _ = self.walk(["", "d", "a", "/", "397B", "/", "esc", "q"])
        self.assertTrue(titles[-1].startswith("All models (4 of 21 files;"))

    def test_settings_is_a_menu_that_never_moves_on_by_itself(self):
        # browse, the top suggestion, its context; settings: KV cache, q8_0 (a fit, no question),
        # parallel requests, 1; then continue
        answers = ["", "d", "1", "", "2", "2", "3", "2", "6", "", "", "", ""]
        titles, selection = self.walk(answers)
        settings = [t for t in titles if t.startswith("Settings for ")]
        self.assertEqual(len(settings), 3)  # shown again after each change
        self.assertEqual((selection.candidate.kv_type, selection.candidate.slots), ("q8_0", 1))
        text = self.out.getvalue()
        self.assertNotIn("Use these settings?", text)
        self.assertIn("KV cache (memory for the conversation so far):", titles)
        self.assertRegex(text, r"q8_0\s+\d+\.\d GiB\s+yes\s+\d+\.\d GiB\s+about half the KV")
        self.assertRegex(text, r"KV cache\s+q8_0 \(UNTESTED with Ornith\)")
        self.assertIn("Memory with these settings: ", text)
        # Continue is last, below a blank line, and outside the columns.
        self.assertRegex(text, r"5  Port\s+18400\n\n6  Continue with these settings\n")
        self.assertRegex(text, r"\n1  Context            262k")

    def test_esc_or_context_on_settings_returns_to_the_context_screen(self):
        context = "Context size (the longest prompt the model can read at once):"
        # Esc on settings, Enter on the context screen; then the Context row, 128k; continue
        answers = ["", "d", "1", "", "esc", "", "1", "3", "6", "", "", ""]
        titles, selection = self.walk(answers)
        self.assertEqual(titles.count(context), 3)
        self.assertEqual(selection.candidate.context, 131072)
        after = titles[titles.index(context, titles.index(context) + 1) + 1]
        self.assertTrue(after.startswith("Settings for "))

    def test_the_port_must_be_a_free_port_in_range(self):
        import socket

        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            taken = busy.getsockname()[1]
            # settings: port 80 (refused), port <taken> (refused), port 18555; continue
            answers = ["", "d", "1", "", "5", "80", "5", str(taken), "5", "18555", "6"]
            _, selection = self.walk([*answers, "", "", ""], port=None)
        text = self.out.getvalue()
        self.assertIn("'80' is not a port from 1024 to 65535; keeping", text)
        self.assertIn(f"Port {taken} is in use; keeping", text)
        self.assertEqual(selection.port, 18555)

    def test_pickers_apply_at_once_and_say_what_matches(self):
        # best fit, filters, source: community only (Enter applies and leaves), then
        # likes: c clears it to any; size: tick 9B, see the count, Esc; back, quit
        answers = ["", "d", "b", "f", "4", "3", "8", "c", "1", "1", "esc", "esc", "q"]
        titles, _ = self.walk(answers)
        text = self.out.getvalue()
        self.assertIn("Showing: 9B; 1 file matches, 1 fit this machine.", text)
        self.assertIn("Clear (any size)  c", text)
        self.assertNotIn("Done  d", text)
        self.assertIn("Filters: size 9B; community only. Search: none.", text)

    def test_b_and_a_switch_lists_and_esc_returns_to_suggested(self):
        # browse, best fit list (b), All models (a), best fit list (b), back, quit
        titles, _ = self.walk(["", "d", "b", "a", "b", "esc", "q"])
        lists = [t.split(" (")[0] for t in titles[1:]]
        self.assertEqual(
            lists,
            ["Suggested for this machine:", "Best fit for this machine", "All models",
             "Best fit for this machine", "Suggested for this machine:"],
        )  # fmt: skip
        self.assertIn("Best fit list  b", self.out.getvalue())
        self.assertIn("Back           Esc", self.out.getvalue())
        self.assertIn(
            "3 more files are too big for this machine. Press 'a' to see them in All models.",
            self.out.getvalue(),
        )

    def test_a_search_narrows_both_lists_and_never_reorders(self):
        # browse, search from Suggested (opens All models), b for the best fit list, quit
        titles, _ = self.walk(["", "d", "/", "397B", "b", "q"])
        self.assertTrue(titles[2].startswith("All models (4 of 21 files;"))
        self.assertTrue(titles[3].startswith("Best fit for this machine (2 files;"))
        text = self.out.getvalue()
        listing = text[
            text.index("All models (4 of 21") : text.index("Best fit for this machine (2")
        ]
        quants = re.findall(r"^\d\s+Ornith 1\.5 397B\s+\w+\s+mit\s+(\S+)", listing, re.M)
        self.assertEqual(quants, ["Q8_0", "Q4_K_M", "IQ2_XXS", "IQ1_S"])  # finest first, fit or not
        self.assertIn("Search: 397B.", text)
        self.assertIn("2 more files are too big for this machine", text)

    def test_filters_narrow_the_lists_and_clear_with_c(self):
        # browse, best fit, filters, size, 35B and 122B, done, back, clear, quit
        # Each tick applies at once; Esc leaves the picker with them kept.
        titles, _ = self.walk(["", "d", "b", "f", "1", "2", "3", "esc", "esc", "c", "q"])
        self.assertTrue(titles[-2].startswith("Best fit for this machine (8 files;"))
        self.assertTrue(titles[-1].startswith("Best fit for this machine (18 files;"))
        text = self.out.getvalue()
        self.assertIn("Filters (9 files match; 8 of them fit this machine):", text)
        self.assertIn("Filters: size 35B or 122B. Search: none.", text)
        self.assertIn("1 more file is too big for this machine. Press 'a' to see it", text)
        self.assertRegex(text, r"2\s+\[x\] 35B\s+7")

    def test_version_and_quant_pickers_tick_and_c_clears(self):
        # best fit, filters, version: tick 1.0, back; quant: tick 8 bits, clear, tick 16 bits,
        # back; back to the list, quit
        answers = ["", "d", "b", "f", "2", "2", "esc", "3", "2", "c", "1", "esc", "esc", "q"]
        titles, _ = self.walk(answers)
        text = self.out.getvalue()
        self.assertIn("Version (Enter ticks or unticks; nothing ticked means any version):", titles)
        self.assertRegex(text, r"1\s+\[ \] 1\.5\s+\d+\n2\s+\[x\] 1\.0")
        self.assertIn("Filters: version 1.0; 16 bits. Search: none.", text)
        self.assertTrue(titles[-1].startswith("Best fit for this machine (0 files;"))
        self.assertIn("Nothing matches the search and filters. Press c to clear them.", text)

    def test_filters_last_until_setup_ends(self):
        # browse, best fit, filters, source, community only, back, main menu, browse, best fit
        titles, _ = self.walk(["", "d", "b", "f", "4", "3", "esc", "m", "d", "b", "q"])
        self.assertEqual(titles.count("What next?"), 2)
        self.assertTrue(titles[-1].startswith("Best fit for this machine (3 files;"))
        self.assertIn("Filters: community only. Search: none.", self.out.getvalue())

    def test_nothing_that_fits_says_where_the_matches_are(self):
        titles, _ = self.walk(["", "d", "/", "397B Q8_0", "b", "q"])
        self.assertTrue(titles[-1].startswith("Best fit for this machine (0 files;"))
        self.assertIn(
            "Nothing that fits this machine matches. 1 file matches in All models (too big): "
            "press 'a'.",
            self.out.getvalue(),
        )

    def test_every_list_row_can_be_picked_and_says_how_it_fits(self):
        import datetime

        from ornith_launcher.browse import ListFilters, build_lists

        wizard = self.wizard([])
        plan = wizard.system_check()
        lists = build_lists(plan.ranked, ListFilters(), "", datetime.date(2026, 10, 27))
        fit_labels = {wizard._cells(c)[7] for c in lists.best_fit}
        all_labels = {wizard._cells(c, lightest=True)[6] for c in lists.all_models}
        self.assertEqual(fit_labels, {"yes", "stretch"})  # never too big in the best fit list
        self.assertEqual(all_labels, {"yes", "stretch", "too big"})
        self.assertEqual(len(lists.all_models) - len(lists.best_fit), lists.too_big)
        self.assertTrue(all(c.context == 32768 and not c.vision for c in lists.all_models))

    def test_best_for_small_memory_lists_rows_by_their_combined_score(self):
        import re

        from ornith_launcher.browse import BEST_FIT_SORTS, highest_scoring, sort_rows
        from ornith_launcher.planner.score import Scorer

        position = next(i for i, s in enumerate(BEST_FIT_SORTS) if s.by == "score")
        try:  # disclaimer, browse, best fit list, sort, this one, then quit
            self.wizard(["", "d", "b", "s", str(position + 1), "q"]).run()
        except Cancelled:
            pass
        text = self.out.getvalue()
        self.assertIn("best for small memory, a combined score; page 1):", text)
        scores = [int(n) for n in re.findall(r"Score\s+(\d+): memory", text)]
        self.assertTrue(scores)
        self.assertIn("(a heuristic, not measured)", " ".join(text.split()))  # wrapped notes
        # Among the rows that fit, the highest score comes first.
        wizard = self.wizard([])
        plan = wizard.system_check()
        scorer = Scorer.for_candidates(plan.ranked)
        picks = highest_scoring(plan.ranked, scorer).values()
        rows = sort_rows(list(picks), BEST_FIT_SORTS[position], scorer)
        fitting = [scorer.score(c).total for c in rows if c.fits]
        self.assertEqual(fitting, sorted(fitting, reverse=True))
        # Each file shows its highest-scoring setting among those the list offers.
        from ornith_launcher.planner.rank import Filters

        listed = Filters(show_uncensored=False)
        top = rows[0]
        same_file = [
            c
            for c in plan.ranked
            if c.entry.file.path == top.entry.file.path and listed.accepts(c) and c.fits
        ]
        best = max(scorer.score(c).total for c in same_file)
        self.assertAlmostEqual(scorer.score(top).total, best)

    def test_a_32k_context_says_it_is_short_for_coding_agents(self):
        wizard = self.wizard(["", "d", "", "1", "", "", ""])  # ... the 35B, 32k, defaults
        selection = wizard.run()
        self.assertEqual(selection.candidate.context, 32768)
        self.assertIn("short for coding agents", self.out.getvalue())

    def test_a_full_disk_asks_for_another_folder(self):
        save_settings(self.app.paths, Settings(disclaimer_version=99))
        good = self.home / "big-disk" / "models"
        good.parent.mkdir()  # a drive mounted there, with room
        # Free space comes from the host, so a test never depends on the machine's disk.
        self.app.host.disk_free = lambda path: None if "big-disk" in path else 3 * GIB
        selection = self.wizard(["d", "", "", "", "", str(good), "", ""]).run()
        text = self.out.getvalue()
        self.assertIn("Not enough free space: the download needs 36.0 GiB plus 2 GiB spare", text)
        self.assertIn("please enter another one", text)
        self.assertEqual(selection.models_dir, good)

    def test_cancel_through_the_cli(self):
        self.app.read_line = lambda prompt: "n"
        code = cli.main(["setup", "--plain"], app=self.app)
        self.assertEqual(code, 1)
        self.assertIn("Setup stopped", self.out.getvalue())

    def run_setup_after_wizard(self, selection, finish_setup) -> int:
        from ornith_launcher import setup_profile

        def end_of_input(prompt):
            raise EOFError

        self.app.read_line = end_of_input
        with mock.patch.object(Wizard, "run", return_value=selection):
            with mock.patch.object(setup_profile, "finish_setup", finish_setup):
                return cli.main(["setup", "--plain"], app=self.app)

    def test_quitting_at_launch_now_keeps_the_profile(self):
        from tests.runtime_fixtures import profile

        saved = profile(self.home)
        selection = mock.Mock(model_paths=[], mmproj_path=None)
        code = self.run_setup_after_wizard(selection, mock.Mock(return_value=saved))
        self.assertEqual(code, 0)
        self.assertIn(f"ornith-launcher launch {saved.name}", self.out.getvalue())
        self.assertNotIn("Setup stopped", self.out.getvalue())

    def test_quitting_after_a_download_says_the_model_is_kept(self):
        model = self.home / "models" / "ornith-ai" / "repo@abc1234" / "m.gguf"
        selection = mock.Mock(needs_download=True, model_paths=[model], mmproj_path=None)
        code = self.run_setup_after_wizard(selection, mock.Mock(side_effect=Cancelled()))
        self.assertEqual(code, 1)
        self.assertIn(f"The downloaded model stays in {model.parent}", self.out.getvalue())
        self.assertNotIn("Nothing else was changed", self.out.getvalue())


class MemoryColumnsTest(unittest.TestCase):
    """Plan left, reserve used and free after on the context screen."""

    GIB = 1024**3

    def candidate(self, *needs: tuple[Pool, float]):
        from types import SimpleNamespace

        from ornith_launcher.planner.memory_model import MemoryEstimate
        from ornith_launcher.planner.rank import PoolNeed

        estimate = MemoryEstimate(int(60 * self.GIB), 0, 0, 0, 0, 0)
        return SimpleNamespace(
            needs=[PoolNeed(pool, int(gib * self.GIB)) for pool, gib in needs], estimate=estimate
        )

    def setUp(self):
        # 100 GiB usable after the system's 20 GiB: a plan of 85 GiB and a 15 GiB reserve.
        self.pool = Pool(PoolKind.UNIFIED, "Unified", 120 * self.GIB, 20 * self.GIB)

    def test_each_kind_of_row(self):
        from ornith_launcher.wizard import memory_cells

        rows = {
            "fits": (80, ("5.0 GiB", "none", "20.0 GiB")),
            "stretch": (90, ("0.0 GiB", "5.0 of 15.0 GiB", "10.0 GiB")),
            "too big": (103, ("0.0 GiB", "all 15.0 GiB", "3.0 GiB short")),
        }
        for kind, (need, cells) in rows.items():
            with self.subTest(kind):
                self.assertEqual(memory_cells(self.candidate((self.pool, need))), cells)

    def test_free_after_counts_memory_other_apps_can_give_back(self):
        from ornith_launcher.wizard import memory_cells

        # 100 GiB usable once other apps close, though they hold 30 GiB of it now.
        busy = Pool(PoolKind.UNIFIED, "Unified", 120 * self.GIB, 20 * self.GIB, 70 * self.GIB)
        self.assertEqual(memory_cells(self.candidate((busy, 60))), ("25.0 GiB", "none", "40.0 GiB"))

    def test_a_mac_recommends_a_context_that_leaves_a_margin(self):
        from types import SimpleNamespace

        from ornith_launcher.planner.rank import Fit, PoolNeed
        from ornith_launcher.wizard import recommended_context

        def rows(pool):
            # 20 GiB usable, a plan of 17 GiB: 128k leaves 2.5 GiB, 262k only 0.5 GiB.
            return [
                SimpleNamespace(
                    fit=Fit.FITS,
                    context=k,
                    native=262144,
                    needs=[PoolNeed(pool, int(g * self.GIB))],
                )
                for k, g in ((131072, 14.5), (262144, 16.5))
            ]

        mac = Pool(PoolKind.UNIFIED, "Unified", 28 * self.GIB, 8 * self.GIB, margin=self.GIB)
        linux = Pool(PoolKind.UNIFIED, "Unified", 28 * self.GIB, 8 * self.GIB)
        self.assertEqual(recommended_context(rows(mac)), 0)
        self.assertEqual(recommended_context(rows(linux)), 1)

    def test_a_split_machine_shows_its_fullest_pool(self):
        from ornith_launcher.wizard import memory_cells

        vram = Pool(PoolKind.VRAM, "GPU", 24 * self.GIB, 4 * self.GIB)  # 20 usable, 3 in reserve
        # RAM keeps more GiB in plan, but the GPU is fuller (90% against 50%), so it decides.
        c = self.candidate((vram, 18), (self.pool, 50))
        self.assertEqual(memory_cells(c), ("0.0 GiB", "1.0 of 3.0 GiB", "2.0 GiB"))

    def test_the_notice_names_the_reserve_and_a_shared_tested_label(self):
        from ornith_launcher.planner.rank import Level
        from ornith_launcher.wizard import memory_notice

        text = memory_notice(self.candidate((self.pool, 80)), Level.ESTIMATED)
        self.assertIn("Setup plans up to 85.0 GiB and keeps 15.0 GiB (15%) in reserve", text)
        self.assertTrue(text.endswith("All memory figures here are ESTIMATED."))
        self.assertNotIn("fullest", text)
        vram = Pool(PoolKind.VRAM, "GPU", 24 * self.GIB, 4 * self.GIB)
        split = memory_notice(self.candidate((vram, 18), (self.pool, 50)))
        self.assertEqual(split.count("in reserve"), 2)  # one line per pool
        self.assertIn("Each row shows its fullest memory pool.", split)
        self.assertNotIn("All memory figures", split)

    def test_the_notice_names_a_gpu_limit_below_what_the_system_leaves(self):
        from ornith_launcher.wizard import memory_notice

        mac = Pool(PoolKind.UNIFIED, "Unified", 32 * self.GIB, 8 * self.GIB, limit=21 * self.GIB)
        text = memory_notice(self.candidate((mac, 10)))
        self.assertIn("32.0 GiB in total. The system uses about 8.0 GiB", text)
        self.assertIn("the GPU may use at most 21.0 GiB, so 21.0 GiB is usable", text)
        self.assertNotIn("at most", memory_notice(self.candidate((self.pool, 80))))


class PieceTest(unittest.TestCase):
    def test_mac_note_for_i_quants_and_bf16_vision_on_m1(self):
        from types import SimpleNamespace

        from ornith_launcher.wizard import mac_note

        def file(quant, vision=False, mmproj="mmproj-BF16.gguf"):
            repo = SimpleNamespace(best_mmproj=lambda: SimpleNamespace(path=mmproj))
            entry = SimpleNamespace(classification=SimpleNamespace(quant=quant), repo=repo)
            return SimpleNamespace(entry=entry, vision=vision)

        self.assertEqual(mac_note(file("Q4_K_M"), "Apple M1 Max"), "")
        self.assertIn("I-quants usually generate a little slower", mac_note(file("IQ4_XS"), ""))
        self.assertTrue(mac_note(file("IQ4_XS"), "").endswith("(ESTIMATED)"))
        bf16 = mac_note(file("Q8_0", vision=True), "Apple M1 Max")
        self.assertEqual(
            bf16, "Its vision file is BF16, which M1 GPUs may run more slowly (ESTIMATED)"
        )
        self.assertEqual(mac_note(file("Q8_0", vision=True), "Apple M2 Max"), "")
        self.assertEqual(mac_note(file("Q8_0", vision=True, mmproj="mmproj-F16.gguf"), "M1"), "")

    def test_yes_no_questions_name_their_default(self):
        prompts = []
        out = io.StringIO()
        prompter = PlainPrompter(read_line=lambda p: prompts.append(p) or "", out=out)
        self.assertTrue(prompter.confirm("Keep this folder?"))
        self.assertFalse(prompter.confirm("Launch now?", default=False))
        self.assertEqual(
            prompts,
            ["Keep this folder? [Y/n] (default: yes) ", "Launch now? [y/N] (default: no) "],
        )

    def test_launch_now_defaults_to_no(self):
        from ornith_launcher import setup_profile
        from tests.runtime_fixtures import profile

        with tempfile.TemporaryDirectory() as tmp:
            app = mock.Mock()
            prompter = PlainPrompter(read_line=lambda prompt: "", out=io.StringIO())
            saved = profile(Path(tmp))
            saved.integrations["desktop_copy"] = "/Desktop/Ornith nine.app"
            with mock.patch.object(setup_profile, "Launcher") as launcher:
                with mock.patch.object(PlainPrompter, "_clear_for_quit") as clear:
                    self.assertEqual(setup_profile.launch_now(app, prompter, saved), 0)
            launcher.assert_not_called()  # Enter alone does not start the server
            # Setup ends on a clear screen that says how to start the model.
            clear.assert_called_once()
            self.assertEqual(
                prompter.out.getvalue().splitlines(),
                [
                    "",  # before the question, as every question has
                    f"Saved the profile {saved.name}.",
                    f"Start it any time with: ornith-launcher launch {saved.name}",
                    "Or double-click its launch icon: /Desktop/Ornith nine.app",
                ],
            )

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
        # The default folder is visible, so the notice can suggest it.
        self.assertIsNone(hidden_notice(Path("/h/models/ornith-launcher"), Path("/h"), "darwin"))
        self.assertIn(
            "for example /h/models/ornith-launcher",
            hidden_notice(Path("/h/.x"), Path("/h"), "linux"),
        )
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
        letters_only = iter(["", "1", "b"])
        letters = PlainPrompter(read_line=lambda p: next(letters_only), out=out)
        picked = letters.choose("Next?", [], [], keys={"e": "enter", "b": "browse"})
        self.assertEqual(picked, "b")
        self.assertIn("Please enter e, b or q.", out.getvalue())
        self.assertNotIn("1 to 0", out.getvalue())
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
        lines = render_list("Title", ["Name", "N"], choices, 1, 0, {"a": "show all"}, 60, 20)
        plain = [re.sub(r"\x1b\[[0-9]*m", "", line) for line in lines]
        self.assertEqual(plain[:3], ["Title", "", "  Name    N"])
        self.assertEqual(lines[2], "  \x1b[1mName    N\x1b[0m")  # bold headers
        self.assertEqual(plain[3], "")
        self.assertTrue(lines[5].startswith("\x1b[7m> second"))
        # Each header starts in the same column as its values.
        self.assertEqual(plain[2].rindex("N"), plain[4].rindex("1"))
        self.assertEqual(plain[2].index("Name"), plain[4].index("first"))
        self.assertEqual(
            plain[-8:],
            [
                "  more",
                "",
                "  Actions",
                "",
                "  Scroll    ↑/↓",
                "  Choose    Enter",
                "  Show all  a",
                "  Quit      q",
            ],
        )
        # Actions are plain text: dim is only for rows that cannot be chosen.
        self.assertEqual(lines[-4:], plain[-4:])
        self.assertIn("\x1b[2m", lines[6])  # the "off" row
        self.assertIn(
            "Up/Down", render_list("T", ["N"], [Choice(("x",))], 0, 0, {}, 60, 20, "Up/Down")[-3]
        )
        # A long list keeps the footer on screen: title, header, rows, blank, detail, footer.
        many = [Choice((str(i),)) for i in range(50)]
        self.assertEqual(len(render_list("T", ["N"], many, 0, 0, {"a": "all"}, 60, 20)), 20)
        self.assertFalse(supports_full_screen(io.StringIO(), io.StringIO(), {"TERM": "xterm"}))

    def test_model_names_link_to_their_cards(self):
        url = "https://huggingface.co/ornith-ai/x"
        choices = [Choice(("Ornith 1.5 9B", "Q8_0"), link=url), Choice(("Ornith ? 9B", "Q4_K_M"))]
        click = "Ctrl+click the name"
        lines = render_list("T", ["Model", "Quant"], choices, 0, 0, {}, 60, 24, click=click)
        self.assertIn(f"\x1b]8;;{url}\x1b\\Ornith 1.5 9B\x1b]8;;\x1b\\", lines[4])
        self.assertNotIn("\x1b]8", lines[5])  # no repo known, no link
        visible = [re.sub(r"\x1b\]8;;[^\x1b]*\x1b\\|\x1b\[[0-9]*m", "", line) for line in lines]
        self.assertEqual(visible[2].index("Quant"), visible[4].index("Q8_0"))
        self.assertIn("  Open model card  Ctrl+click the name", visible)
        # The hint only shows when a row has a link, and links need a click hint.
        unlinked = render_list("T", ["Model", "Quant"], choices[1:], 0, 0, {}, 60, 24, click=click)
        self.assertFalse(any("Open model card" in line for line in unlinked))
        self.assertFalse(
            any(
                "\x1b]8" in line for line in render_list("T", ["M", "Q"], choices, 0, 0, {}, 60, 24)
            )
        )

    def test_highlighted_url_is_printed_for_terminals_without_links(self):
        url = "https://huggingface.co/ornith-ai/x"
        choices = [Choice(("Ornith 1.5 9B",), detail="native", link=url), Choice(("other",))]
        lines = render_list("T", ["Model"], choices, 0, 0, {}, 60, 24, click="hint")
        plain = [re.sub(r"\x1b\[[0-9]*m", "", line) for line in lines]
        # The note, a blank line, then "URL - <link>" in full, with URL in bold.
        at = plain.index("  native")
        self.assertEqual(plain[at + 1 : at + 3], ["", f"  URL - {url}"])
        self.assertIn(f"  \x1b[1mURL\x1b[0m - {url}", lines)
        # A highlighted row without a link leaves the same space empty, so nothing jumps.
        other = render_list("T", ["Model"], choices, 1, 0, {}, 60, 24, click="hint")
        self.assertNotIn(f"  URL - {url}", [re.sub(r"\x1b\[[0-9]*m", "", x) for x in other])
        self.assertEqual(len(other), len(lines))

    def test_pages_turn_with_letters_or_arrows(self):
        from contextlib import nullcontext

        from ornith_launcher.ui import tui

        lines = render_list(
            "T",
            ["N"],
            [Choice(("x",))],
            0,
            0,
            {"n": "next page", "p": "previous page"},
            60,
            24,
            pages=tui.alternates(True, True),
        )
        plain = [re.sub(r"\x1b\[[0-9]*m", "", line) for line in lines]
        self.assertIn("  Next page      n or →", plain)
        self.assertIn("  Previous page  p or ←", plain)
        self.assertEqual(tui.alternates(False, True, arrows="Up/Down"), {"n": "Right"})
        keys = iter(["left", "right"])  # no previous page, so the left arrow does nothing
        with mock.patch.object(tui, "read_key", lambda timeout=None: next(keys)):
            with mock.patch.object(tui, "raw_keys", nullcontext):
                prompter = tui.TuiPrompter(out=io.StringIO())
                picked = prompter.choose("T", ["N"], [Choice(("x",))], next_page=True)
        self.assertEqual(picked, "n")
        with self.assertRaises(ValueError):
            PlainPrompter().choose("T", ["N"], [], keys={"p": "custom path"}, previous_page=True)

    def test_enter_on_a_greyed_out_row_says_why_instead_of_doing_nothing(self):
        from contextlib import nullcontext

        from ornith_launcher.ui import tui

        keys = iter(["down", "enter", "up", "enter"])
        out = io.StringIO()
        with mock.patch.object(tui, "read_key", lambda timeout=None: next(keys)):
            with mock.patch.object(tui, "raw_keys", nullcontext):
                choices = [Choice(("on",)), Choice(("off",), enabled=False)]
                picked = tui.TuiPrompter(out=out).choose("T", ["N"], choices)
        self.assertEqual(picked, 0)
        frames = out.getvalue().split(tui.CLEAR)
        message = "That option is not available here; its note says why."
        self.assertIn(message, frames[3])  # drawn right after Enter on "off"
        self.assertNotIn(message, frames[4])  # and gone after the next key

    def test_warnings_wrap_on_a_terminal_only(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True

        text = "word " * 60
        for out, wrapped in ((Terminal(), True), (io.StringIO(), False)):
            PlainPrompter(out=out).paragraph(text.strip())
            lines = out.getvalue().splitlines()
            self.assertEqual(len(lines) > 1, wrapped)
            self.assertTrue(all(len(line) <= 100 for line in lines) or not wrapped)

    def test_rows_are_cut_to_a_narrow_terminal_instead_of_wrapping(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True

        cells = ("Ornith 1.5 122B", "103.5 GiB", "5.5 of 17.3 GiB", "1.9 GiB short", "too big")
        for out, cut in ((Terminal(), True), (io.StringIO(), False)):
            prompter = PlainPrompter(read_line=lambda prompt: "1", out=out)
            with mock.patch.dict("os.environ", {"COLUMNS": "40", "LINES": "24"}):
                prompter.choose(
                    "T", ["Model", "Total", "Reserve", "Free", "Verdict"], [Choice(cells)]
                )
            rows = [line for line in out.getvalue().splitlines() if line.startswith(("#", "1 "))]
            self.assertEqual(len(rows), 2)
            self.assertEqual(all(len(row) <= 40 for row in rows), cut, rows)
            self.assertEqual(rows[0].index("Total"), rows[1].index("103.5"))  # still aligned

    def test_wide_and_control_characters_keep_columns_in_line(self):
        from ornith_launcher.ui.plain import table, text_width

        rows = [
            ["模型 9B", "Q8_0", "yes"],
            ["Ornith\t9B\n", "Q4_K_M", "stretch"],
            ["é", "Q2_K", "no"],
        ]
        lines = table(["Model", "Quant", "Fits"], rows).splitlines()
        # The CJK row takes two columns per character, so count columns, not characters.
        columns = {
            text_width(line[: line.index(q)])
            for line, q in zip(lines[1:], ("Q8_0", "Q4_K_M", "Q2_K"))
        }
        self.assertEqual(columns, {text_width(lines[0][: lines[0].index("Quant")])})
        self.assertNotIn("\t", "".join(lines))
        self.assertEqual(len(lines), 4)  # the newline in a cell did not add a line
        cut = table(["Model", "Quant"], [["模型模型模型", "Q8_0"]], max_width=9).splitlines()
        self.assertTrue(all(text_width(line) <= 9 for line in cut), cut)

    def test_full_screen_rows_line_up_under_the_header_at_any_width(self):
        from ornith_launcher.ui.plain import text_width

        choices = [
            Choice(("Ornith 1.5 35B-A3B", "Q8_0", "46.7 GiB", "yes")),
            Choice(("模型 9B", "Q4_K_M", "5.4 GiB", "stretch")),
            Choice(("Ornith\t? 9B", "IQ1_S", "104.7 GiB", "too big")),
        ]
        for width in (120, 40, 25):
            lines = render_list(
                "T", ["Model", "Quant", "Memory", "Fits"], choices, 0, 0, {}, width, 30
            )
            plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines]
            header = next(line for line in plain if line.strip().startswith("Model"))
            rows = [line for line in plain if line[2:].startswith(("Ornith", "模型"))]
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(text_width(line) <= width for line in plain), width)
            if "Quant" in header:
                column = text_width(header[: header.index("Quant")])
                for line, quant in zip(rows, ("Q8_0", "Q4_K_M", "IQ1_S")):
                    if quant in line:
                        self.assertEqual(text_width(line[: line.index(quant)]), column)

    def test_details_wrap_under_bold_labels_and_end_with_the_url(self):
        from ornith_launcher.ui.prompts import detail_lines

        url = "https://huggingface.co/quant-uploader/Ornith-1.5-397B-GGUF"
        path = "quant-uploader/Ornith-1.5-397B-GGUF/Ornith-1.5-397B-Q6_K-00001-of-00009.gguf"
        reason = (
            "Needs 324.6 GiB; Unified memory (NVIDIA GB10) has 115.0 GiB at most after the "
            "system's own use"
        )
        choice = Choice(("x",), info=(("File", path), ("Too big", reason)), link=url)
        lines = detail_lines(choice, 50, lambda text: f"*{text}*")
        self.assertTrue(lines[0].startswith("*File*     quant-uploader/"))
        too_big = next(i for i, line in enumerate(lines) if line.startswith("*Too big*"))
        self.assertTrue(lines[too_big + 1].startswith("         ") and lines[too_big + 1].strip())
        self.assertTrue(all(len(line.replace("*", "")) <= 50 for line in lines[:-1]))
        self.assertEqual(lines[-2:], ["", f"*URL* - {url}"])  # a blank line, then the full URL
        plain = detail_lines(Choice(("x",), detail="word " * 30), 40, str)
        self.assertGreater(len(plain), 1)
        self.assertEqual(detail_lines(Choice(("x",), link=url), 40, str), [f"URL - {url}"])

    def test_full_screen_details_keep_the_list_still(self):
        long_info = (("File", "a/" + "b" * 150), ("Too big", "needs 324.6 GiB " * 6))
        choices = [Choice(("one",), info=long_info, link="https://x/1"), Choice(("two",))]
        first = render_list("T", ["N"], choices, 0, 0, {}, 60, 40, click="hint")
        second = render_list("T", ["N"], choices, 1, 0, {}, 60, 40, click="hint")
        self.assertEqual(len(first), len(second))  # the tallest detail sets the space
        links = r"\x1b\]8;;[^\x1b]*\x1b\\"  # the name's OSC 8 link takes no columns
        plain = [re.sub(r"\x1b\[[0-9;]*m", "", re.sub(links, "", line)) for line in first]
        self.assertIn("  URL - https://x/1", plain)
        self.assertIn("\x1b[1mToo big\x1b[0m", "".join(first))
        self.assertTrue(all(len(line) <= 60 for line in plain))

    def test_esc_is_back_in_plain_menus_and_b_is_not(self):
        from ornith_launcher.ui.prompts import BACK

        for answer in ("\x1b", "esc", "ESC"):
            prompter = PlainPrompter(read_line=lambda prompt, a=answer: a, out=io.StringIO())
            self.assertEqual(prompter.choose("T", ["N"], [Choice(("x",))], back=True), BACK)
        answers = iter(["b", "1"])
        out = io.StringIO()
        prompter = PlainPrompter(read_line=lambda prompt: next(answers), out=out)
        self.assertEqual(prompter.choose("T", ["N"], [Choice(("x",))], back=True), 0)
        self.assertIn("Please enter 1, or a letter.", out.getvalue())
        self.assertIn("Back  Esc", out.getvalue())

    def test_an_action_row_is_bold_apart_and_does_not_widen_the_columns(self):
        choices = [
            Choice(("Context", "262k")),
            Choice(("Port", "18400")),
            Choice(("Continue with these settings", ""), action=True),
        ]
        for selected in (0, 2):
            lines = render_list("T", ["Setting", "Value"], choices, selected, 0, {}, 60, 30)
            plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines]
            self.assertIn("  Setting  Value", plain)  # sized by the data rows only
            at = next(i for i, line in enumerate(plain) if "Continue with" in line)
            self.assertEqual(plain[at - 1], "")
            self.assertEqual(plain[at - 2].rstrip()[-5:], "18400")
            self.assertIn("\x1b[1mContinue with these settings", lines[at])
        self.assertTrue(lines[at].startswith("\x1b[7m> \x1b[1m"))  # highlighted and bold

    def full_screen_keys(self, *keys):
        """A full-screen prompter that reads ``keys`` one by one, as from the keyboard."""
        from contextlib import ExitStack, nullcontext

        from ornith_launcher.ui import tui

        pressed = iter(keys)
        stack = ExitStack()
        stack.enter_context(mock.patch.object(tui, "read_key", lambda timeout=None: next(pressed)))
        stack.enter_context(mock.patch.object(tui, "raw_keys", nullcontext))
        self.addCleanup(stack.close)
        return tui.TuiPrompter(out=io.StringIO())

    def test_typed_answers_on_the_full_screen_take_esc_at_once(self):
        from ornith_launcher.ui.prompts import BACK, is_back

        prompter = self.full_screen_keys("~", "/", "m", "x", "backspace", "é", "enter")
        self.assertEqual(prompter.text("Path"), "~/mé")  # typed, one deleted, UTF-8 kept
        self.assertTrue(is_back(self.full_screen_keys("a", "esc").text("Path", back=True)))
        self.assertEqual(self.full_screen_keys("a", "esc").text("Port", "18400"), "18400")
        self.assertEqual(self.full_screen_keys("x", "\x15", "y", "enter").text("Q"), "y")
        self.assertFalse(self.full_screen_keys("esc").confirm("Delete these?", default=True))
        self.assertIsNone(self.full_screen_keys("esc").confirm("Go ahead?", back=True))
        self.assertFalse(
            self.full_screen_keys("esc").typed_confirmation("Uncensored.", "I understand")
        )
        with self.assertRaises(Cancelled):
            self.full_screen_keys("eof").text("Path")
        self.assertEqual(BACK, "esc")

    def test_yes_no_questions_are_bold_where_the_terminal_shows_it(self):
        from ornith_launcher.ui.plain import text_width

        prompter = self.full_screen_keys("y", "enter")
        self.assertTrue(prompter.confirm("Delete these?", default=False))
        self.assertIn("\x1b[1mDelete these?\x1b[0m [y/N] (default: no) ", prompter.out.getvalue())
        # The bold codes take no columns, so the answer keeps its full room.
        self.assertEqual(text_width("\x1b[1mDelete these?\x1b[0m"), len("Delete these?"))
        plain = io.StringIO()
        asked = []
        PlainPrompter(read_line=lambda prompt: asked.append(prompt) or "y", out=plain).confirm("Q?")
        self.assertEqual(asked, ["Q? [Y/n] (default: yes) "])  # pipes, logs, --plain: no codes

    def test_bold_words_do_not_shorten_a_wrapped_line(self):
        from ornith_launcher.ui.plain import text_width, wrap_styled

        text = "\x1b[1mOpenCode\x1b[0m " + "word " * 30
        lines = wrap_styled(text.strip(), 40, "  ")
        self.assertTrue(all(text_width(line) <= 40 for line in lines))
        self.assertEqual(text_width(lines[0]), 40)  # "  OpenCode" and 6 words: full width
        self.assertTrue(all(line.startswith("  ") for line in lines))
        self.assertEqual(wrap_styled("", 40), [])

    def test_a_long_answer_scrolls_and_a_long_question_gets_its_own_line(self):
        from ornith_launcher.ui.tui import _tail

        self.assertEqual(_tail("short", 10), "short")
        self.assertEqual(_tail("/home/user/models/ornith", 10), "…ls/ornith")  # 10 columns
        prompter = self.full_screen_keys("y", "enter")
        with mock.patch("shutil.get_terminal_size", return_value=os.terminal_size((40, 20))):
            self.assertEqual(
                prompter.text("A question that is far too long for this narrow window"), "y"
            )
        self.assertIn("far too long for this narrow window:\n", prompter.out.getvalue())

    def test_menus_of_actions_take_one_key_without_enter(self):
        from ornith_launcher.ui.keys import utf8_length
        from ornith_launcher.ui.prompts import BACK

        keys = {"d": "browse and download", "p": "custom model path"}
        self.assertEqual(
            self.full_screen_keys("x", "D").choose("What next?", [], [], keys=keys), "d"
        )
        back = self.full_screen_keys("esc").choose("Uninstall", [], [], keys=keys, back=True)
        self.assertEqual(back, BACK)
        with self.assertRaises(Cancelled):
            self.full_screen_keys("esc").choose("What next?", [], [], keys=keys)
        self.assertEqual([utf8_length(b) for b in (0x41, 0xC3, 0xE2, 0xF0)], [1, 2, 3, 4])

    def test_notices_keep_their_line_breaks(self):
        from ornith_launcher.ui.tui import notice_lines

        class Terminal(io.StringIO):
            def isatty(self):
                return True

        notice = "First part.\n" + "word " * 30 + "\nLast part."
        lines = notice_lines(notice, 40)
        self.assertEqual(lines[0], "First part.")
        self.assertEqual(lines[-1], "Last part.")
        self.assertTrue(all(len(line) <= 38 for line in lines))
        for out in (Terminal(), io.StringIO()):
            PlainPrompter(out=out).paragraph(notice)
            self.assertTrue(out.getvalue().startswith("First part.\nword "))
            self.assertTrue(out.getvalue().endswith("\nLast part.\n"))

    def test_a_resized_window_is_drawn_again(self):
        import os
        from contextlib import nullcontext

        from ornith_launcher.ui import tui

        sizes = iter([os.terminal_size((100, 30))] * 2 + [os.terminal_size((50, 30))] * 9)
        keys = iter(["", "", "enter"])  # two quiet polls, the second after the resize
        out = io.StringIO()
        with mock.patch.object(tui, "read_key", lambda timeout=None: next(keys)):
            with mock.patch.object(tui.shutil, "get_terminal_size", lambda fallback: next(sizes)):
                with mock.patch.object(tui, "raw_keys", nullcontext):
                    picked = tui.TuiPrompter(out=out).choose("T", ["N"], [Choice(("x",))])
        self.assertEqual(picked, 0)
        frames = out.getvalue().split(tui.CLEAR)[1:]
        self.assertEqual(len(frames), 2)  # the first poll changed nothing, the resize redrew

    def test_full_screen_steps_clear_the_screen_but_keep_scrollback(self):
        from ornith_launcher.ui.tui import CLEAR, TuiPrompter

        out = io.StringIO()
        TuiPrompter(out=out).new_screen("Summary")
        self.assertTrue(out.getvalue().startswith(CLEAR))
        self.assertIn("Summary", out.getvalue())
        self.assertNotIn("\x1b[3J", out.getvalue())  # the scrollback stays
        plain = io.StringIO()
        PlainPrompter(out=plain).new_screen("Summary")
        self.assertEqual(plain.getvalue(), "\nSummary\n\n")  # a blank line after the title

    def test_screen_layout_helpers(self):
        from ornith_launcher.ui.plain import key_values
        from ornith_launcher.ui.tui import BOLD, TuiPrompter

        self.assertEqual(
            key_values([("Model", "Ornith"), ("Free space", "3 GiB")]),
            ["  Model:       Ornith", "  Free space:  3 GiB"],
        )
        out = io.StringIO()
        prompter = PlainPrompter(read_line=lambda p: "", out=out)
        prompter.say("Folder: x")
        prompter.confirm("Keep it?")
        prompter.text("Port", "18400")
        self.assertEqual(out.getvalue(), "Folder: x\n\n\n")  # a blank line before each question
        out = io.StringIO()
        PlainPrompter(out=out).section("Setups")
        self.assertEqual(out.getvalue(), "\nSetups\n")
        tui = io.StringIO()
        TuiPrompter(out=tui).section("Setups")
        self.assertIn(f"{BOLD}Setups", tui.getvalue())
        quiet = io.StringIO()
        PlainPrompter(out=quiet, assume_yes=True).confirm("Keep it?")
        self.assertEqual(quiet.getvalue(), "")  # --yes asks nothing, so prints nothing

    def test_compact_counts(self):
        from ornith_launcher.ui.plain import compact_count

        cases = {0: "0", 554: "554", 999: "999", 1000: "1k", 12345: "12.3k"}
        cases.update({999_960: "1M", 3_860_800: "3.9M", 2_000_000_000: "2B"})
        for value, text in cases.items():
            self.assertEqual(compact_count(value), text, value)

    def test_escape_goes_back_where_back_is_offered(self):
        from contextlib import nullcontext

        from ornith_launcher.ui import tui

        lines = render_list(
            "T",
            ["N"],
            [Choice(("x",))],
            0,
            0,
            {"esc": "back"},
            60,
            24,
        )
        self.assertIn("  Back    Esc", [re.sub(r"\x1b\[[0-9]*m", "", x) for x in lines])
        for back, expected in ((True, "esc"), (False, Cancelled)):
            with mock.patch.object(tui, "read_key", lambda timeout=None: "esc"):
                with mock.patch.object(tui, "raw_keys", nullcontext):
                    prompter = tui.TuiPrompter(out=io.StringIO())
                    if expected is Cancelled:
                        with self.assertRaises(Cancelled):
                            prompter.choose("T", ["N"], [Choice(("x",))], back=back)
                    else:
                        self.assertEqual(
                            prompter.choose("T", ["N"], [Choice(("x",))], back=back), expected
                        )

    def test_click_hint(self):
        xterm = {"TERM": "xterm-256color"}
        self.assertEqual(click_hint(xterm, "linux"), "Ctrl+click the name or URL")
        self.assertEqual(click_hint(xterm, "darwin"), "Cmd+click the name or URL")
        self.assertEqual(click_hint({"TERM": "linux"}, "linux"), "")

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
