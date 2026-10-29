from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ornith_launcher import cli, launch
from ornith_launcher.app import App
from ornith_launcher.config import resolve_paths
from ornith_launcher.integrations import tool_windows
from ornith_launcher.profiles import load_profile, save_profile
from ornith_launcher.runtime.docker import DockerRuntime
from ornith_launcher.ui.prompts import PlainPrompter
from tests.runtime_fixtures import FakeDocker, FakeHttp, profile


class ToolWindowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = FakeDocker(env={"HOME": str(self.root), "DISPLAY": ":0"})
        self.host.programs.update({"konsole": "/usr/bin/konsole", "opencode": "/usr/bin/opencode"})
        self.out = io.StringIO()
        self.app = App(
            host=self.host,
            paths=resolve_paths({}, self.root, "linux"),
            out=self.out,
            err=io.StringIO(),
            os_name="linux",
            machine="aarch64",
            read_line=lambda prompt: "",
        )
        self.wrapper = self.root / ".local" / "bin" / "ornith-claude-ornith-1.5-35b-q8-262k"
        self.wrapper.parent.mkdir(parents=True)
        self.wrapper.write_text("#!/bin/sh\n")
        self.profile = profile(self.root)
        self.profile.terminal = {"id": "konsole"}
        self.profile.integrations.update(
            claude_code={"wrapper": str(self.wrapper)},
            opencode={"config": "x", "provider": "ornith-launcher"},
        )
        save_profile(self.app.paths, self.profile)
        self.http = FakeHttp()
        patcher = mock.patch.object(
            launch,
            "make_runtime",
            lambda app: DockerRuntime(app.host, self.http, sleep=lambda s: None),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def prompter(self, *answers):
        replies = iter(answers)
        self.asked = []

        def read_line(prompt):
            self.asked.append(prompt)
            return next(replies)

        return PlainPrompter(read_line=read_line, out=self.out)

    # The setup question ---------------------------------------------------------------------

    def test_both_tools_can_be_chosen_and_open_in_home_by_default(self):
        tool_windows.offer(self.app, self.prompter("4", ""), self.profile, headless=False)
        self.assertEqual(self.profile.terminal["open_tools"], ["claude_code", "opencode"])
        self.assertEqual(self.profile.terminal["tools_folder"], str(self.root.resolve()))
        self.assertIn("stay open when the server stops", self.out.getvalue())

    def test_one_tool_is_a_yes_or_no_question_that_defaults_to_no(self):
        self.profile.integrations.pop("opencode")
        tool_windows.offer(self.app, self.prompter(""), self.profile, headless=False)
        self.assertNotIn("open_tools", self.profile.terminal)
        self.assertIn("Open Claude Code when this model starts?", "".join(self.asked))

    def test_a_folder_that_does_not_exist_is_asked_again(self):
        project = self.root / "project"
        project.mkdir()
        answers = ("3", str(self.root / "missing"), str(project))
        tool_windows.offer(self.app, self.prompter(*answers), self.profile, headless=False)
        self.assertEqual(self.profile.terminal["open_tools"], ["opencode"])
        self.assertEqual(self.profile.terminal["tools_folder"], str(project.resolve()))
        self.assertIn("is not a folder", self.out.getvalue())

    def test_nothing_is_asked_without_a_desktop_or_a_connected_tool(self):
        self.profile.terminal["open_tools"] = ["opencode"]
        tool_windows.offer(self.app, self.prompter(), self.profile, headless=True)
        self.assertNotIn("open_tools", self.profile.terminal)
        self.profile.integrations.clear()
        tool_windows.offer(self.app, self.prompter(), self.profile, headless=False)
        self.assertEqual(self.asked, [])

    def test_a_tool_no_longer_connected_is_not_opened(self):
        self.profile.terminal["open_tools"] = ["claude_code", "opencode"]
        self.profile.integrations.pop("claude_code")
        self.assertEqual(tool_windows.chosen(self.profile), ["opencode"])

    # Opening them at launch -----------------------------------------------------------------

    def choose_both(self):
        self.profile.terminal.update(open_tools=["claude_code", "opencode"], tools_folder="/work")
        save_profile(self.app.paths, self.profile)

    def test_launch_opens_each_tool_in_its_own_window_once_ready(self):
        self.choose_both()
        self.assertEqual(cli.main(["launch", "--here", "--no-monitor"], app=self.app), 0)
        konsole = [argv for argv in self.host.spawned if argv[0] == "konsole"]
        self.assertEqual(
            konsole[0],
            (
                "konsole",
                "-p",
                "tabtitle=Claude Code: ornith-1.5-35b-q8-262k",
                "-e",
                "sh",
                "-c",
                f"cd /work && exec {self.wrapper}",
            ),
        )
        self.assertEqual(
            konsole[1][-1],
            "cd /work && exec /usr/bin/opencode -m ornith-launcher/ornith-1.5-35b-q8_0",
        )
        text = self.out.getvalue()
        self.assertLess(text.index("is ready at"), text.index("Opened Claude Code"))
        self.assertIn("Opened OpenCode in a separate Konsole window, in /work.", text)

    def test_no_tools_skips_them_this_time(self):
        self.choose_both()
        self.assertEqual(
            cli.main(["launch", "--here", "--no-monitor", "--no-tools"], app=self.app), 0
        )
        self.assertEqual([a for a in self.host.spawned if a[0] == "konsole"], [])

    def test_the_server_window_keeps_no_tools(self):
        self.choose_both()
        self.host.programs["konsole"] = "/usr/bin/konsole"
        self.assertEqual(cli.main(["launch", "--no-tools"], app=self.app), 0)
        self.assertEqual(
            self.host.spawned[0][-4:], ("launch", self.profile.name, "--here", "--no-tools")
        )

    def test_a_missing_tool_names_the_fix(self):
        self.choose_both()
        self.wrapper.unlink()
        cli.main(["launch", "--here", "--no-monitor"], app=self.app)
        self.assertIn(
            f"Claude Code was not found, so it was not opened. Install and connect it again with: "
            f"ornith-launcher setup --integrations {self.profile.name}",
            self.out.getvalue(),
        )

    def test_without_a_desktop_the_command_is_shown(self):
        self.choose_both()
        del self.host._env["DISPLAY"]
        cli.main(["launch", "--here", "--no-monitor"], app=self.app)
        self.assertIn(
            f"Start Claude Code in another terminal with: cd /work && exec {self.wrapper}",
            self.out.getvalue(),
        )
        self.assertEqual([a for a in self.host.spawned if a[0] == "konsole"], [])

    def test_setup_integrations_asks_again_and_saves(self):
        with mock.patch("ornith_launcher.integrations.setup.choose", return_value="both"):
            with mock.patch("ornith_launcher.integrations.setup.configure"):
                answers = iter(["2", ""])  # Claude Code only, home folder
                self.app.read_line = lambda prompt: next(answers)
                code = cli.main(
                    ["setup", "--plain", "--integrations", self.profile.name], app=self.app
                )
        self.assertEqual(code, 0)
        saved = load_profile(self.app.paths, self.profile.name)
        self.assertEqual(saved.terminal["open_tools"], ["claude_code"])


if __name__ == "__main__":
    unittest.main()
