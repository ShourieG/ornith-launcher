from __future__ import annotations

import io
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
        models = self.home / ".ornith-launcher" / "models"
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
        self.assertIn("This folder is hidden", text)
        self.assertIn("No Ornith models found in the usual places.", text)
        # Each file says what it is for; the vision file can be left out in the settings.
        self.assertIn("Vision:             on: adds a 0.8 GiB vision file so the model", text)
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
        folder = self.home / ".ornith-launcher" / "models" / "ornith-ai"
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
            "2",  # 128k context
            "n",  # change the settings
            "",  # KV cache: default
            "n",  # 1 parallel request
            "",  # vision: as it was
            "18450",  # port
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
        folder = self.home / ".ornith-launcher" / "models" / "ornith-ai"
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
            "n",  # change the settings
            "",  # KV cache: default
            "",  # 4 parallel requests
            "n",  # vision off
            "",  # port
            "",  # keep the models folder
            "",  # go ahead
        ]
        wizard = self.wizard(answers)
        prompts = []
        read_line = wizard.prompter.read_line
        wizard.prompter.read_line = lambda prompt: prompts.append(prompt) or read_line(prompt)
        selection = wizard.run()
        text = self.out.getvalue()
        self.assertIn("Turn vision (image input) on? It adds a 0.8 GiB download.", "".join(prompts))
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
        models = self.home / ".ornith-launcher" / "models"
        part = self.stopped_download(models / "ornith-ai" / "Ornith-1.5-9B-GGUF@abc1234", "m.gguf")
        self.app.read_line = lambda prompt: "q"
        self.assertEqual(cli.main(["setup", "--plain"], app=self.app), 1)
        self.assertIn("A stopped download is kept: resume it with r", self.out.getvalue())
        self.assertTrue(Path(str(part) + ".part").exists())

    def test_a_stopped_download_no_longer_listed_says_how_to_remove_it(self):
        save_settings(self.app.paths, Settings(disclaimer_version=disclaimer_text()[0]))
        models = self.home / ".ornith-launcher" / "models"
        self.stopped_download(models / "gone-uploader" / "Removed-Repo-GGUF@1234567", "x.gguf")
        with self.assertRaises(Cancelled):
            self.wizard(["r", "1", "b", "q"]).run()
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
                # disclaimer, browse, only uncensored, decline, again, accept, quit
                self.wizard(["", "d", "o", "n", "o", "y", "q"]).run()
        titles = [title for title, _, _ in shown]
        self.assertEqual(titles[1:3], ["Suggested for this machine:"] * 2)  # declined: no list
        title, notice, choices = shown[3]
        self.assertTrue(title.startswith("Uncensored models only ("))
        self.assertIn("You alone are responsible", notice)
        self.assertIn("can miss some or include others", notice)
        self.assertIn("You alone are responsible", self.out.getvalue())
        self.assertTrue(choices)
        plan = self.wizard([]).system_check()
        uncensored = {
            (c.entry.repo.id, c.entry.file.path)
            for c in plan.ranked
            if c.entry.classification.uncensored
        }
        self.assertTrue(
            all(c.link.split("huggingface.co/")[1] in {r for r, _ in uncensored} for c in choices)
        )
        self.assertTrue(load_settings(self.app.paths).show_uncensored)
        self.assertIn("in later runs too, until you hide them again", self.out.getvalue())

    def test_lists_say_when_uncensored_models_are_shown(self):
        save_settings(self.app.paths, Settings(disclaimer_version=99, show_uncensored=True))
        titles, _ = self.walk(["d", "a", "q"])
        self.assertIn("Suggested for this machine (uncensored shown):", titles)
        all_models = [t for t in titles if t.startswith("All models (")]
        self.assertIn(", uncensored shown;", all_models[0])

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
        # disclaimer, browse, all models, sort, by likes, first row, back, first row again
        titles, selection = self.walk(["", "d", "a", "s", "6", "1", "b", "1", "", "", "", ""])
        self.assertIsNotNone(selection)
        context = "Context size (the longest prompt the model can read at once):"
        index = titles.index(context)
        self.assertTrue(titles[index - 1].startswith("All models ("))
        self.assertIn("likes, most first", titles[index + 1])  # same list, same order
        self.assertEqual(titles[index + 2], context)

    def test_main_menu_and_back_lead_to_the_first_menu(self):
        # browse, all models, first row, main menu, browse, back (suggested -> first menu), quit
        titles, _ = self.walk(["", "d", "a", "1", "m", "d", "b", "q"])
        self.assertEqual(titles.count("What next?"), 3)
        self.assertEqual(titles[-1], "What next?")
        after_main = titles[titles.index("What next?", 1) + 1]
        self.assertEqual(after_main, "Suggested for this machine:")  # browsing starts afresh
        # The quick search ran once; coming back does not repeat it.
        self.assertEqual(self.out.getvalue().count("Looking for Ornith models"), 1)
        self.assertEqual(self.out.getvalue().count("No Ornith models found"), 1)

    def test_back_from_a_local_model_shows_the_first_menu(self):
        settings = Settings(disclaimer_version=disclaimer_text()[0])
        save_settings(self.app.paths, settings)
        fake_model(self.home / "models" / "my-ornith.gguf", "9B", name="Ornith 1.5 9B")
        titles, _ = self.walk(["1", "y", "b", "q"])
        found = "Ornith models found on this machine (used where they are, never moved):"
        self.assertEqual(titles, [found, titles[1], found])

    def test_model_path_is_used_once(self):
        save_settings(self.app.paths, Settings(disclaimer_version=disclaimer_text()[0]))
        folder = self.home / "elsewhere"
        fake_model(folder / "Ornith-1.0-9B-Q8_0.gguf", "9B")
        titles, _ = self.walk(["y", "b", "q"], model_path=folder)
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
        self.assertEqual(
            screens, ["Checking this machine...", "Settings", "Models folder", "Summary"]
        )

    def test_uninstall_is_offered_only_when_the_launcher_installed_something(self):
        titles, _ = self.walk(["", "q"])
        self.assertNotIn("uninstall", self.out.getvalue().lower())
        from ornith_launcher.installed import record

        record(self.app.paths, "shortcuts", str(self.home / "Desktop" / "x.desktop"))
        self.out.truncate(0)
        # first menu, uninstall, back from the uninstall menu, first menu again, quit
        titles, _ = self.walk(["u", "b", "q"])
        self.assertIn("Uninstall or remove setups", self.out.getvalue())
        self.assertEqual(titles, ["What next?", "Uninstall", "What next?"])

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
        answers = iter(["l", "1", "b", "l", "2"])

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
        answers = ["p", "", "b", "p", str(folder), "y", "", "", ""]
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
        # A wrong answer or a bare Enter asks again; only b goes back.
        wrong = self.wizard(["ok", "", "b"])
        self.assertFalse(wrong._consents(Selection(entry=uncensored.entry, candidate=uncensored)))
        self.assertEqual(self.out.getvalue().count("(quotes are optional) to continue, or b"), 2)
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


class PieceTest(unittest.TestCase):
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
            with mock.patch.object(setup_profile, "Launcher") as launcher:
                self.assertEqual(setup_profile.launch_now(app, prompter, profile(Path(tmp))), 0)
            launcher.assert_not_called()  # Enter alone does not start the server

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
                "more",
                "",
                "  Actions",
                "",
                "  Scroll    ↑/↓",
                "  Choose    Enter",
                "  Show all  a",
                "  Quit      q",
            ],
        )
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
        self.assertEqual(lines[lines.index("native") + 1], url)
        # A highlighted row without a link leaves the URL line empty.
        lines = render_list("T", ["Model"], choices, 1, 0, {}, 60, 24, click="hint")
        self.assertNotIn(url, lines)
        self.assertEqual(lines[-9:-7], ["", ""])  # detail, URL

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
        with mock.patch.object(tui, "read_key", lambda: next(keys)):
            with mock.patch.object(tui, "raw_keys", nullcontext):
                prompter = tui.TuiPrompter(out=io.StringIO())
                picked = prompter.choose("T", ["N"], [Choice(("x",))], next_page=True)
        self.assertEqual(picked, "n")
        with self.assertRaises(ValueError):
            PlainPrompter().choose("T", ["N"], [], keys={"p": "custom path"}, previous_page=True)

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

    def test_sort_orders_keep_fitting_models_first(self):
        from types import SimpleNamespace

        from ornith_launcher.wizard import SORTS, sorted_rows

        def row(name, fits, memory, size, likes, downloads):
            repo = SimpleNamespace(likes=likes, downloads=downloads, best_mmproj=lambda: None)
            entry = SimpleNamespace(repo=repo, file=SimpleNamespace(size=size))
            estimate = SimpleNamespace(total=memory)
            return SimpleNamespace(
                name=name, fits=fits, estimate=estimate, entry=entry, vision=False
            )

        rows = [
            row("best", True, 40, 30, 10, 100),
            row("small", True, 10, 5, 500, 9),
            row("huge", False, 300, 250, 900, 10**6),
            row("popular", True, 20, 15, 50, 10**5),
        ]
        order = {label: i for i, (label, _, _) in enumerate(SORTS)}

        def names(label):
            return [r.name for r in sorted_rows(rows, order[label])]

        self.assertEqual(names("best first"), ["best", "small", "huge", "popular"])
        self.assertEqual(names("memory, smallest first"), ["small", "popular", "best", "huge"])
        self.assertEqual(names("memory, largest first"), ["best", "popular", "small", "huge"])
        self.assertEqual(names("download size, smallest first")[0], "small")
        self.assertEqual(names("download size, largest first")[:3], ["best", "popular", "small"])
        self.assertEqual(names("likes, most first"), ["small", "popular", "best", "huge"])
        self.assertEqual(names("downloads, most first"), ["popular", "best", "small", "huge"])

    def test_escape_goes_back_where_back_is_offered(self):
        from contextlib import nullcontext

        from ornith_launcher.ui import tui

        lines = render_list(
            "T",
            ["N"],
            [Choice(("x",))],
            0,
            0,
            {"b": "back"},
            60,
            24,
            pages=tui.alternates(False, False, back=True),
        )
        self.assertIn("  Back    b or Esc", [re.sub(r"\x1b\[[0-9]*m", "", x) for x in lines])
        for back, expected in ((True, "b"), (False, Cancelled)):
            with mock.patch.object(tui, "read_key", lambda: "esc"):
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
