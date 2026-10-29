from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from ornith_launcher import cli
from ornith_launcher.app import App
from ornith_launcher.config import resolve_paths
from ornith_launcher.installed import load_installed, record
from ornith_launcher.profiles import list_profiles, load_profile, save_profile
from ornith_launcher.runtime.docker import digest_reference
from ornith_launcher.uninstall import has_installs, plan_removal
from tests.runtime_fixtures import IMAGE, FakeDocker, profile


class UninstallTest(unittest.TestCase):
    """Only what the profiles and the install record say the launcher made is removed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.host = FakeDocker(env={"HOME": str(self.home)})
        self.out = io.StringIO()
        self.app = App(
            host=self.host,
            paths=resolve_paths({}, self.home, "linux"),
            out=self.out,
            err=io.StringIO(),
            os_name="linux",
            machine="aarch64",
            full_screen=False,
        )
        self.models = self.home / "models" / "ornith-launcher"
        # A setup with a downloaded model, a desktop icon and a menu entry.
        self.downloaded = self.download("ornith-ai/Ornith-1.5-9B-GGUF@abc1234/m-Q8_0.gguf")
        self.icon = self.shortcut(self.home / "Desktop" / "ornith-launcher-nine.desktop")
        self.menu = self.shortcut(
            self.home / ".local/share/applications/ornith-launcher-nine.desktop"
        )
        self.nine = profile(self.home, name="nine")
        self.nine.model.parts = [str(self.downloaded)]
        self.nine.model.path = str(self.downloaded)
        self.nine.model.location = "managed"
        self.nine.integrations.update(desktop_copy=str(self.icon), shortcut=str(self.menu))
        save_profile(self.app.paths, self.nine)
        # A setup using a model found elsewhere: never deleted.
        self.found = profile(self.home, name="found")
        save_profile(self.app.paths, self.found)
        # A download whose setup was stopped, and a file the launcher never made.
        self.loose = self.download("x/y@def5678/loose-Q4_K_M.gguf")
        Path(str(self.loose) + ".part").write_bytes(b"part")
        self.stranger = self.models / "my-own-model.gguf"
        self.stranger.write_bytes(b"mine")
        record(self.app.paths, "images", IMAGE)

    def download(self, relative: str) -> Path:
        path = self.models / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"GGUF" + b"\0" * 64)
        record(self.app.paths, "downloads", str(path))
        return path

    def shortcut(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[Desktop Entry]\n")
        record(self.app.paths, "shortcuts", str(path))
        return path

    def uninstall(self, *answers):
        replies = iter(answers)

        def read_line(prompt):
            try:
                return next(replies)
            except StopIteration:
                raise EOFError from None

        self.app.read_line = read_line
        return cli.main(["uninstall"], app=self.app)

    def test_setup_only_keeps_the_model_it_downloaded(self):
        removal = plan_removal(self.app, [self.nine], keep_models=True)
        self.assertEqual(removal.model_files, [])
        self.assertEqual(removal.kept_by_choice, [self.downloaded])
        # Remove one setup, "nine" (the second), Setup only (the default), confirm.
        self.assertEqual(self.uninstall("1", "2", "", "y"), 0)
        self.assertEqual(list_profiles(self.app.paths), ["found"])
        self.assertFalse(self.icon.exists())
        self.assertFalse(self.menu.exists())
        self.assertTrue(self.downloaded.exists())
        # Still recorded as ours, so "Remove everything" can delete it later.
        self.assertIn(str(self.downloaded), load_installed(self.app.paths).downloads)
        text = self.out.getvalue()
        self.assertIn("profile, launch icon and menu entry; the model stays", text)
        self.assertIn("Kept (you chose to keep the model)\n  " + str(self.downloaded), text)

    def test_one_setup_removes_its_icon_menu_entry_and_download(self):
        removal = plan_removal(self.app, [self.nine])
        self.assertEqual(removal.model_files, [self.downloaded])
        self.assertEqual(set(removal.shortcuts), {self.icon, self.menu})
        self.assertEqual(removal.images, [])  # only "everything" touches the image
        # Remove one setup, the second one ("found" sorts first), setup and model, confirm.
        self.assertEqual(self.uninstall("1", "2", "2", "y"), 0)
        for path in (self.downloaded, self.icon, self.menu):
            self.assertFalse(path.exists(), path)
        self.assertFalse(self.downloaded.parent.exists())  # its empty folder too
        self.assertEqual(list_profiles(self.app.paths), ["found"])
        self.assertTrue(Path(self.found.model.path).exists())
        self.assertTrue(self.loose.exists())
        installed = load_installed(self.app.paths)
        self.assertNotIn(str(self.downloaded), installed.downloads)
        self.assertEqual(installed.shortcuts, [])

    def test_the_coding_tool_entries_are_listed_before_they_are_removed(self):
        import json

        from ornith_launcher.integrations import opencode

        config = self.home / ".config" / "opencode" / "opencode.json"
        config.parent.mkdir(parents=True)
        mine = {"provider": {"my-own": {"models": {"m": {}}}}}
        pictures = self.home / "data" / "opencode" / opencode.PICTURES_NAME
        merged = opencode.merged(mine, self.nine, "ornith-launcher", "Ornith 9B", pictures)
        config.write_text(json.dumps(merged))
        wrapper = self.home / ".local" / "bin" / "ornith-claude-nine"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\n")
        self.nine.integrations.update(
            opencode={"config": str(config), "provider": "ornith-launcher"},
            claude_code={"wrapper": str(wrapper)},
        )
        save_profile(self.app.paths, self.nine)
        # one setup, "nine", setup only, confirm
        self.assertEqual(self.uninstall("1", "2", "1", "y"), 0)
        text = self.out.getvalue()
        self.assertIn("menu entry, OpenCode entry and Claude Code wrapper; the model stays", text)
        listed = text[text.index("Coding-tool settings") :]
        alias = self.nine.runtime.alias
        self.assertIn(f"OpenCode: the model {alias} (provider ornith-launcher) in {config}", listed)
        self.assertIn(f"OpenCode: the picture instructions {pictures}, which go with the", listed)
        self.assertIn(f"Claude Code: {wrapper}", listed)
        left = json.loads(config.read_text())
        self.assertEqual((left["provider"], left.get("instructions")), (mine["provider"], None))
        self.assertFalse(wrapper.exists())

    def only_found(self):
        """Leave just the setup that uses a model found elsewhere."""
        from ornith_launcher.profiles import delete_profile

        delete_profile(self.app.paths, "nine")

    def test_an_existing_model_setup_offers_setup_server_or_both(self):
        self.only_found()
        model = Path(self.found.model.path)
        # one setup, "found", the model server only, confirm
        self.assertEqual(self.uninstall("1", "1", "3", "y"), 0)
        self.assertIn(("docker", "image", "rm", digest_reference(IMAGE)), self.host.calls)
        self.assertIn(
            ("docker", "rm", "-f", "ornith-launcher-found"), self.host.calls
        )  # stopped first
        self.assertEqual(list_profiles(self.app.paths), ["found"])  # the setup stays
        self.assertTrue(model.exists())
        self.assertIn(
            "the model was found on this machine, so it is never deleted", self.out.getvalue()
        )

    def test_both_removes_the_setup_and_the_server_but_never_the_model(self):
        self.only_found()
        model = Path(self.found.model.path)
        self.assertEqual(self.uninstall("1", "1", "4", "y"), 0)
        self.assertEqual(list_profiles(self.app.paths), [])
        self.assertIn(("docker", "image", "rm", digest_reference(IMAGE)), self.host.calls)
        self.assertTrue(model.exists())
        self.assertEqual(load_installed(self.app.paths).images, [])

    def test_the_server_is_not_offered_while_another_setup_needs_it(self):
        # "found" while "nine" uses the same image: picking the server is refused
        self.assertEqual(self.uninstall("1", "1", "3", "1", "y"), 0)
        text = self.out.getvalue()
        self.assertIn("The model server is not offered: nine also needs it.", text)
        self.assertIn("That option is not available here.", text)
        self.assertNotIn(("docker", "image", "rm", digest_reference(IMAGE)), self.host.calls)
        self.assertEqual(list_profiles(self.app.paths), ["nine"])

    def test_a_server_the_launcher_did_not_pull_is_kept(self):
        from ornith_launcher.installed import forget

        self.only_found()
        forget(self.app.paths, "images", IMAGE)
        self.uninstall("1", "1", "esc", "esc", "esc")
        self.assertIn("already on this machine before ornith-launcher", self.out.getvalue())

    def test_a_found_model_that_the_launcher_downloaded_can_be_deleted(self):
        # A finished download picked from setup's first menu was saved as "external".
        self.nine.model.location = "external"
        save_profile(self.app.paths, self.nine)
        # one setup, "nine", Setup and its model, confirm
        self.assertEqual(self.uninstall("1", "2", "2", "y"), 0)
        self.assertNotIn("That option is not available here", self.out.getvalue())
        self.assertFalse(self.downloaded.exists())
        self.assertNotIn(str(self.downloaded), load_installed(self.app.paths).downloads)

    def test_downloaded_here_needs_every_part_recorded(self):
        from ornith_launcher.installed import downloaded_here

        self.assertTrue(downloaded_here(self.app.paths, [str(self.downloaded)]))
        both = [str(self.downloaded), self.found.model.path]
        self.assertFalse(downloaded_here(self.app.paths, both))
        self.assertFalse(downloaded_here(self.app.paths, []))

    def test_a_model_found_elsewhere_is_listed_as_kept(self):
        removal = plan_removal(self.app, [self.found])
        self.assertEqual(removal.model_files, [])
        self.assertEqual(removal.kept_models, [Path(self.found.model.path)])

    def test_all_setups_can_keep_or_delete_their_models(self):
        self.assertEqual(self.uninstall("2", "1", "y"), 0)  # setups only
        self.assertEqual(list_profiles(self.app.paths), [])
        self.assertTrue(self.downloaded.exists())
        save_profile(self.app.paths, self.nine)
        self.assertEqual(self.uninstall("2", "2", "y"), 0)  # setups and their models
        self.assertFalse(self.downloaded.exists())

    def test_all_setups_keep_loose_downloads_and_the_image(self):
        self.assertEqual(self.uninstall("2", "2", "y"), 0)
        self.assertEqual(list_profiles(self.app.paths), [])
        self.assertTrue(self.loose.exists())
        self.assertIn(IMAGE, self.host.images)
        self.assertTrue(has_installs(self.app))  # the loose download and the image remain

    def test_everything_removes_only_what_the_launcher_made(self):
        self.assertEqual(self.uninstall("4", "y"), 0)  # 3 is the unfinished downloads
        for path in (self.downloaded, self.icon, self.menu, self.loose):
            self.assertFalse(path.exists(), path)
        self.assertFalse(Path(str(self.loose) + ".part").exists())
        self.assertTrue(self.stranger.exists())  # never recorded, so never touched
        self.assertTrue(Path(self.found.model.path).exists())
        self.assertIn(("docker", "image", "rm", digest_reference(IMAGE)), self.host.calls)
        self.assertFalse(self.app.paths.config.exists())
        text = self.out.getvalue()
        self.assertIn("  Deleted the llama.cpp image.", text)
        # Grouped under labels, one item per line.
        self.assertIn("\nSetups\n  found, with", text)
        self.assertIn("\n\nLaunch icons and menu entries\n  ", text)
        self.assertIn("\n\nKept (not downloaded by ornith-launcher)\n  ", text)

    def stop_download(self, relative: str) -> Path:
        path = self.download(relative)
        path.rename(Path(str(path) + ".part"))
        return path

    def test_unfinished_downloads_are_removed_on_their_own(self):
        from ornith_launcher.unfinished import choices_file, save_choices

        second = self.stop_download("z/w@9999999/second-Q4_K_M.gguf")
        for download in (self.loose, second):
            save_choices(self.app.paths, download.parent, {"file": download.name})
        # Remove unfinished downloads, the first of the two, confirm.
        self.assertEqual(self.uninstall("3", "1", "y"), 0)
        self.assertFalse(choices_file(self.app.paths, self.loose.parent).exists())
        self.assertTrue(choices_file(self.app.paths, second.parent).exists())
        self.assertIn("Which unfinished download?", self.out.getvalue())
        self.assertFalse(self.loose.exists())
        self.assertFalse(Path(str(self.loose) + ".part").exists())
        self.assertTrue(Path(str(second) + ".part").exists())  # not picked
        self.assertTrue(self.downloaded.exists())  # a setup's finished model stays
        self.assertEqual(sorted(list_profiles(self.app.paths)), ["found", "nine"])
        self.assertNotIn(str(self.loose), load_installed(self.app.paths).downloads)

    def test_only_the_options_that_apply_are_listed(self):
        self.only_found()
        from ornith_launcher.profiles import delete_profile

        delete_profile(self.app.paths, "found")
        self.uninstall("1", "y")  # with no setups, 1 is the unfinished downloads
        text = self.out.getvalue()
        self.assertNotIn("Remove one setup", text)
        self.assertIn("1  Remove unfinished downloads", text)
        self.assertIn("2  Remove everything", text)
        self.assertFalse(Path(str(self.loose) + ".part").exists())
        self.assertTrue(self.app.paths.config.exists())  # not everything

    def test_an_image_other_tags_use_is_kept(self):
        self.host.other_tags = True
        self.uninstall("4", "y")
        self.assertIn("other tags keep it", self.out.getvalue())

    def test_nothing_is_deleted_without_a_yes(self):
        # Enter takes the default (no), the menu comes back, then input ends.
        self.assertEqual(self.uninstall("4", ""), 1)
        self.assertIn("Nothing more was removed.", self.out.getvalue())
        self.assertTrue(self.downloaded.exists())
        self.assertEqual(sorted(list_profiles(self.app.paths)), ["found", "nine"])
        self.assertEqual(
            load_profile(self.app.paths, "nine").integrations["shortcut"], str(self.menu)
        )

    def test_digest_reference(self):
        self.assertEqual(
            digest_reference("ghcr.io/ggml-org/llama.cpp:server-cuda13-b11552@sha256:ab"),
            "ghcr.io/ggml-org/llama.cpp@sha256:ab",
        )
        self.assertEqual(
            digest_reference("localhost:5000/x:t@sha256:ab"), "localhost:5000/x@sha256:ab"
        )
        self.assertEqual(digest_reference("plain:tag"), "plain:tag")


if __name__ == "__main__":
    unittest.main()
