from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ornith_launcher import chat_log, cli, launch
from ornith_launcher.app import App
from ornith_launcher.config import resolve_paths
from ornith_launcher.integrations.shortcuts import desktop_entry, shortcut_path
from ornith_launcher.integrations.terminals import (
    current_terminal,
    detect_terminals,
    runs_in,
)
from ornith_launcher.profiles import (
    Profile,
    ProfileError,
    list_profiles,
    load_profile,
    save_profile,
    suggest_name,
)
from ornith_launcher.runtime import docker as rt
from ornith_launcher.runtime.docker import DockerRuntime
from ornith_launcher.settings import Settings, save_settings
from ornith_launcher.system.host import CommandResult
from ornith_launcher.templates import compat
from tests.fake_host import FakeHost
from tests.runtime_fixtures import IMAGE, FakeDocker, FakeHttp, profile


class ProfileTest(unittest.TestCase):
    def test_round_trip_and_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths({}, Path(tmp), "linux")
            p = profile(Path(tmp))
            save_profile(paths, p)
            again = load_profile(paths, p.name)
            self.assertEqual(again, p)
            self.assertEqual(list_profiles(paths), [p.name])
            with self.assertRaisesRegex(ProfileError, "no profile named 'other'") as caught:
                load_profile(paths, "other")
            self.assertIn(p.name, caught.exception.hint)
            with self.assertRaisesRegex(ProfileError, "not a valid profile name"):
                load_profile(paths, "../etc/passwd")

    def test_rejects_unknown_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = profile(Path(tmp)).to_dict()
        data["schema_version"] = 99
        with self.assertRaisesRegex(ProfileError, "schema"):
            Profile.from_dict(data)

    def test_suggested_names(self):
        self.assertEqual(
            suggest_name("1.5", "35B-A3B", "Q8_0", 262144, []), "ornith-1.5-35b-q8-262k"
        )
        self.assertEqual(suggest_name("1.0", "9B", "Q4_K_M", 1048576, []), "ornith-1.0-9b-q4-1m")
        taken = ["ornith-1.5-9b-q6k-66k"]
        self.assertEqual(suggest_name("1.5", "9B", "Q6_K", 65536, taken), "ornith-1.5-9b-q6k-66k-2")


class TerminalTest(unittest.TestCase):
    def test_detection_order(self):
        programs = {"konsole": "/usr/bin/konsole", "gnome-terminal": "/usr/bin/gnome-terminal"}
        host = FakeHost(env={"TERMINAL": "konsole"}, programs=dict(programs))
        self.assertEqual(detect_terminals(host).default.id, "konsole")
        gnome = FakeHost(
            env={"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"},
            programs={
                **programs,
                "gsettings": "/usr/bin/gsettings",
                "x-terminal-emulator": "/usr/bin/x-terminal-emulator",
            },
            commands={
                (
                    "gsettings",
                    "get",
                    "org.gnome.desktop.default-applications.terminal",
                    "exec",
                ): CommandResult(0, "'x-terminal-emulator'\n", "")
            },
            links={"/usr/bin/x-terminal-emulator": "/usr/bin/gnome-terminal.wrapper"},
        )
        found = detect_terminals(gnome)
        self.assertEqual(found.default.id, "gnome-terminal")
        self.assertEqual({t.id for t in found.installed}, {"konsole", "gnome-terminal"})
        self.assertIsNone(detect_terminals(FakeHost()).default)

    def test_command_and_current_terminal(self):
        host = FakeHost(programs={"konsole": "/usr/bin/konsole"})
        konsole = detect_terminals(host).default
        self.assertEqual(
            konsole.command(["ornith-launcher", "launch", "p", "--here"], "Ornith: p"),
            [
                "konsole",
                "-p",
                "tabtitle=Ornith: p",
                "-e",
                "ornith-launcher",
                "launch",
                "p",
                "--here",
            ],
        )
        self.assertEqual(current_terminal({"KONSOLE_VERSION": "240802"}), "konsole")
        self.assertTrue(runs_in("ptyxis", {"VTE_VERSION": "7600"}))
        self.assertFalse(runs_in("konsole", {"VTE_VERSION": "7600"}))


class ShortcutTest(unittest.TestCase):
    def test_desktop_entry(self):
        entry = desktop_entry("p1", ["/home/u/my apps/ornith-launcher"], "Start Ornith")
        self.assertIn('Exec="/home/u/my apps/ornith-launcher" launch p1', entry)
        self.assertIn("Terminal=false", entry)
        # "This terminal" has no window of its own, so the desktop must open one.
        self.assertIn("Terminal=true", desktop_entry("p1", ["ornith-launcher"], "x", True))
        self.assertNotIn("Actions=", entry)
        # With the chat log: a "Show chat log" action, in its own window or the entry's terminal.
        chat = desktop_entry("p1", ["/opt/ornith-launcher"], "x", chat_action=True)
        self.assertIn("Actions=chat-log;\n", chat)
        self.assertIn("[Desktop Action chat-log]\nName=Show chat log\n", chat)
        self.assertIn("Exec=/opt/ornith-launcher chat-log p1 --window\n", chat)
        in_terminal = desktop_entry("p1", ["/opt/ornith-launcher"], "x", True, chat_action=True)
        self.assertIn("Exec=/opt/ornith-launcher chat-log p1\n", in_terminal)
        self.assertEqual(
            shortcut_path("p1", {}, Path("/home/u")),
            Path("/home/u/.local/share/applications/ornith-launcher-p1.desktop"),
        )


class DesktopFolderTest(unittest.TestCase):
    def test_desktop_folder_from_user_dirs(self):
        from ornith_launcher.integrations.shortcuts import desktop_dir

        home = Path("/home/u")
        self.assertEqual(desktop_dir(None, home), home / "Desktop")
        localised = 'XDG_DESKTOP_DIR="$HOME/Schreibtisch"\n'
        self.assertEqual(desktop_dir(localised, home), home / "Schreibtisch")
        self.assertEqual(desktop_dir('XDG_DESKTOP_DIR="/data/desk"', home), Path("/data/desk"))
        self.assertIsNone(desktop_dir('XDG_DESKTOP_DIR="$HOME/"', home))  # desktop turned off


class CompatTest(unittest.TestCase):
    """Rules on templates written for these tests (no third-party template text)."""

    def template(self, line: str) -> str:
        return "{%- for message in messages %}\n" + line + "\n{%- endfor %}\n"

    def test_each_rule_finds_its_line_once(self):
        for rule in compat.RULES:
            with self.subTest(rule.id):
                template = self.template(rule.find)
                verdict = compat.check(template)
                self.assertIs(verdict.rule, rule)
                self.assertFalse(verdict.tested)  # not one of the real templates
                patched = compat.apply(template, rule)
                self.assertNotIn(rule.find, patched)
                self.assertIn("<|im_start|>system", patched)

    def test_unknown_or_ambiguous_templates_are_left_alone(self):
        self.assertIsNone(compat.check("{{ messages }}").rule)
        rule = compat.RULES[0]
        twice = self.template(rule.find + "\n" + rule.find)
        self.assertIsNone(compat.check(twice).rule)
        with self.assertRaises(ValueError):
            compat.apply(twice, rule)

    def test_reads_the_template_from_a_gguf(self):
        from ornith_launcher.catalog.gguf import ValueType
        from tests.gguf_builder import gguf_bytes, ornith_pairs

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.gguf"
            pairs = ornith_pairs("9B") + [("tokenizer.chat_template", ValueType.STRING, "{{ x }}")]
            path.write_bytes(gguf_bytes(pairs))
            self.assertEqual(compat.read_template(path), "{{ x }}")
            path.write_bytes(gguf_bytes(ornith_pairs("9B")))
            self.assertIsNone(compat.read_template(path))


class LaunchCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = FakeDocker(env={"HOME": str(self.root)})
        self.host.files["/proc/meminfo"] = "MemTotal: 127598608 kB\nMemAvailable: 120000000 kB\n"
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
        self.profile = profile(self.root)
        save_profile(self.app.paths, self.profile)
        self.http = FakeHttp()
        patcher = mock.patch.object(
            launch,
            "make_runtime",
            lambda app: DockerRuntime(app.host, self.http, sleep=lambda s: None),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *args):
        return cli.main(list(args), app=self.app)

    def test_launch_here_starts_warms_and_monitors(self):
        with mock.patch.object(launch.Monitor, "run", return_value=False) as monitor:
            self.assertEqual(self.run_cli("launch", "--here"), 0)
        monitor.assert_called_once()
        text = self.out.getvalue()
        self.assertTrue(text.startswith("=" * 72))  # the stop instructions come first
        self.assertIn("Press Ctrl+C here to stop the server and unload the model", text)
        self.assertIn(f"ornith-launcher stop {self.profile.name}", text)
        self.assertIn("Ready after", text)
        self.assertIn("is ready at http://127.0.0.1:18400", text)
        self.assertTrue(self.http.posts)  # warm-up
        self.assertIn(rt.container_name(self.profile.name), self.host.containers)

    def test_no_banner_without_a_monitor(self):
        self.assertEqual(self.run_cli("launch", "--here", "--no-monitor"), 0)
        self.assertNotIn("Press Ctrl+C", self.out.getvalue())

    def test_launch_opens_the_chosen_terminal(self):
        self.profile.terminal = {"id": "konsole"}
        save_profile(self.app.paths, self.profile)
        self.host.programs["konsole"] = "/usr/bin/konsole"
        self.assertEqual(self.run_cli("launch"), 0)
        spawned = self.host.spawned[0]
        self.assertEqual(
            spawned[:4], ("konsole", "-p", f"tabtitle=Ornith: {self.profile.name}", "-e")
        )
        self.assertEqual(spawned[-3:], ("launch", self.profile.name, "--here"))
        self.assertEqual(self.host.containers, {})  # the new window starts the server

    # The chat log ------------------------------------------------------------------------

    def chat_profile(self):
        """A desktop with Konsole, and a profile that asked for the chat log."""
        self.host._env.update(DISPLAY=":0", XDG_CURRENT_DESKTOP="KDE")
        self.host.programs["konsole"] = "/usr/bin/konsole"
        self.profile.terminal = {"id": "konsole", "chat_log": True}
        save_profile(self.app.paths, self.profile)

    def chat_windows(self):
        return [s for s in self.host.spawned if "chat-log" in s]

    def proxy_runs(self, pid=5151, port=18400):
        """The chat log proxy, as its run file and process leave it."""
        import json

        path = chat_log.proxy_file(self.app, self.profile.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": pid, "port": port, "token": "t"}))
        self.host.processes[pid] = f"/opt/ornith-launcher chat-proxy {self.profile.name}"

    def window_runs(self, pid=4242):
        path = chat_log.window_file(self.app, self.profile.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{pid}\n")
        self.host.processes[pid] = (
            f"python3 -m ornith_launcher chat-log {self.profile.name} --in-window"
        )

    def launcher(self):
        return launch.Launcher(
            self.app, launch.make_runtime(self.app), launch.prompter_for(self.app)
        )

    def test_the_chat_log_starts_with_the_server(self):
        self.chat_profile()
        with mock.patch.object(chat_log, "start_proxy", return_value=None) as start_proxy:
            with mock.patch.object(chat_log, "port_free", return_value=True):
                with mock.patch.object(launch, "Monitor") as monitor:
                    self.assertEqual(self.run_cli("launch", "--here"), 0)
        start_proxy.assert_called_once()
        run = next(c for c in self.host.calls if c[:3] == ("docker", "run", "-d"))
        self.assertIn("127.0.0.1::8080", run)  # the proxy has the profile's port
        [window] = self.chat_windows()
        title = f"tabtitle=Ornith chat log: {self.profile.name}"
        self.assertEqual(window[:4], ("konsole", "-p", title, "-e"))
        self.assertEqual(window[-3:], ("chat-log", self.profile.name, "--in-window"))
        text = self.out.getvalue()
        self.assertIn("Requests and replies show in the chat log window.", text)
        self.assertIn("Press c and Enter here", text)
        self.assertIn("Opened the chat log in a separate Konsole window.", text)
        self.assertIn("is ready at http://127.0.0.1:18400", text)  # clients keep the URL
        hooks = monitor.call_args.kwargs
        hooks["reopen_chat"]()  # c and Enter in the server window opens it again
        self.assertEqual(len(self.chat_windows()), 2)
        # Stopping the server from the monitor ends the proxy and closes the window.
        self.proxy_runs()
        self.window_runs()
        hooks["on_stop"]()
        self.assertEqual(sorted(self.host.terminated), [4242, 5151])

    def test_no_chat_log_unless_set_up(self):
        with mock.patch.object(launch, "Monitor") as monitor:
            self.run_cli("launch", "--here")
        self.assertIsNone(monitor.call_args.kwargs["reopen_chat"])
        run = next(c for c in self.host.calls if c[:3] == ("docker", "run", "-d"))
        self.assertIn("127.0.0.1:18400:8080", run)
        self.run_cli("stop")
        self.chat_profile()
        with mock.patch.object(launch, "Monitor"):
            self.run_cli("launch", "--here", "--no-chat-log")
        self.assertEqual(self.chat_windows(), [])
        self.assertFalse(any("chat-proxy" in s for s in self.host.spawned))
        self.assertNotIn("Press c and Enter", self.out.getvalue())

    def test_the_chat_log_choice_travels_to_the_server_window(self):
        self.chat_profile()
        self.run_cli("launch", "--no-chat-log")
        self.assertEqual(
            self.host.spawned[0][-4:], ("launch", self.profile.name, "--here", "--no-chat-log")
        )

    def test_a_port_taken_by_another_program_is_named(self):
        self.chat_profile()
        with mock.patch.object(chat_log, "port_free", return_value=False):
            self.assertEqual(self.run_cli("launch", "--here"), 1)
        text = self.out.getvalue() + self.app.err.getvalue()
        self.assertIn("port 18400 is in use by another program", text)
        self.assertIn(f"launch {self.profile.name} --no-chat-log", text)
        self.assertEqual(self.host.containers, {})

    def test_a_proxy_that_cannot_start_stops_the_new_server(self):
        self.chat_profile()
        with mock.patch.object(chat_log, "start_proxy", return_value="port 18400 is in use"):
            with mock.patch.object(chat_log, "port_free", return_value=True):
                self.assertEqual(self.run_cli("launch", "--here"), 1)
        self.assertIn("the chat log could not start", self.app.err.getvalue())
        self.assertEqual(self.host.containers, {})

    def test_a_server_running_with_other_settings_is_never_stopped(self):
        self.run_cli("launch", "--here", "--no-monitor")
        self.chat_profile()
        with mock.patch.object(chat_log, "start_proxy", return_value=None):
            self.assertEqual(self.run_cli("launch", "--here"), 1)
        self.assertIn("the chat log is on for one and off", self.app.err.getvalue())
        self.assertIn(rt.container_name(self.profile.name), self.host.containers)

    def test_a_proxy_that_ended_is_started_again(self):
        self.chat_profile()
        self.run_cli("launch", "--here", "--no-monitor", "--no-chat-log")
        launcher = self.launcher()
        with mock.patch.object(chat_log, "start_proxy", return_value=None) as start_proxy:
            launcher.keep_chat_log(self.profile)
            self.proxy_runs()
            launcher.keep_chat_log(self.profile)  # running: left alone
        start_proxy.assert_called_once()
        self.assertIn("The chat log proxy had stopped; it runs again.", self.out.getvalue())

    def test_stop_ends_the_chat_log_too(self):
        self.run_cli("launch", "--here", "--no-monitor")
        self.proxy_runs()
        self.window_runs()
        self.assertEqual(self.run_cli("stop", self.profile.name), 0)
        self.assertEqual(sorted(self.host.terminated), [4242, 5151])

    def test_reopening_replaces_a_chat_log_window_that_is_still_open(self):
        self.chat_profile()
        self.window_runs()
        self.assertTrue(self.launcher().open_chat_window(self.profile))
        self.assertEqual(self.host.terminated, [4242])
        self.assertIn("Opened a new chat log window in place of the old one.", self.out.getvalue())
        # A stale run file naming another program's process is never acted on.
        chat_log.window_file(self.app, self.profile.name).write_text("4243\n")
        self.host.processes[4243] = "vim notes.txt"
        self.launcher().open_chat_window(self.profile)
        self.assertEqual(self.host.terminated, [4242])
        self.assertEqual(len(self.chat_windows()), 2)

    def test_without_a_desktop_the_chat_log_command_is_suggested(self):
        self.chat_profile()
        del self.host._env["DISPLAY"]
        self.assertFalse(self.launcher().open_chat_window(self.profile))
        self.assertIn(f"ornith-launcher chat-log {self.profile.name}", self.out.getvalue())
        self.assertEqual(self.chat_windows(), [])

    def test_the_chat_log_window_shows_events_and_closes_when_the_server_stops(self):
        import os

        self.run_cli("launch", "--here", "--no-monitor")
        self.proxy_runs()
        claimed = []

        def connect(info):
            self.assertEqual(info["token"], "t")
            claimed.append(chat_log.window_file(self.app, self.profile.name).read_text())
            yield {"type": "notice", "id": 0, "text": "waiting for the first request"}
            yield {
                "type": "request",
                "id": 1,
                "time": "12:00:00",
                "path": "/v1/messages",
                "summary": "3 messages",
                "parts": [["You", "fix the test"]],
            }
            yield {"type": "text", "id": 1, "kind": "reply", "text": "Done."}
            yield {"type": "end", "id": 1, "status": 200, "seconds": 1.5, "tokens": 3}
            self.host.containers.clear()  # the server stops; the proxy ends with it

        prompts = []
        self.app.read_line = lambda prompt: prompts.append(prompt) or ""
        runtime = launch.make_runtime(self.app)
        code = chat_log.follow(self.app, runtime, self.profile, in_window=True, connect=connect)
        self.assertEqual(code, 0)
        self.assertEqual(claimed, [f"{os.getpid()}\n"])  # the window records itself while open
        self.assertFalse(chat_log.window_file(self.app, self.profile.name).exists())
        text = self.out.getvalue()
        self.assertIn("closes only the chat log", text)
        self.assertIn("=== #1  12:00:00  /v1/messages  (3 messages)\nYou:\n  fix the test\n", text)
        self.assertIn("Reply:\n  Done.\n--- #1 done in 1.5 s, 3 tokens\n", text)
        self.assertIn(f"The server {self.profile.name} stopped.", text)
        self.assertEqual(prompts, [])  # it closes by itself
        self.assertEqual(self.host.terminated, [])

    def test_ctrl_c_in_the_chat_log_closes_only_the_chat_log(self):
        self.run_cli("launch", "--here", "--no-monitor")
        self.proxy_runs()

        def connect(info):
            raise KeyboardInterrupt
            yield

        runtime = launch.make_runtime(self.app)
        self.assertEqual(chat_log.follow(self.app, runtime, self.profile, connect=connect), 0)
        self.assertIn("Closed the chat log. The server keeps running.", self.out.getvalue())
        self.assertIn(rt.container_name(self.profile.name), self.host.containers)

    def test_a_server_without_the_chat_log_says_how_to_turn_it_on(self):
        self.run_cli("launch", "--here", "--no-monitor")
        self.assertEqual(self.run_cli("chat-log", self.profile.name), 1)
        self.assertIn(f"launch {self.profile.name} --chat-log", self.out.getvalue())

    def test_the_chat_log_waits_for_a_starting_server_then_says_how_to_start_it(self):
        slept = []
        runtime = launch.make_runtime(self.app)
        code = chat_log.follow(self.app, runtime, self.profile, sleep=slept.append)
        self.assertEqual(code, 1)
        self.assertEqual(len(slept), chat_log.WAIT_SECONDS)
        self.assertIn(
            f"Start it with: ornith-launcher launch {self.profile.name}", self.out.getvalue()
        )

    def test_logs_follows_the_server_log_and_never_stops_the_server(self):
        self.assertEqual(self.run_cli("logs", self.profile.name), 1)
        self.assertIn("is not running", self.out.getvalue())
        self.run_cli("launch", "--here", "--no-monitor")
        self.assertEqual(self.run_cli("logs", self.profile.name), 0)
        name = rt.container_name(self.profile.name)
        self.assertIn(("docker", "logs", "--follow", "--tail", "200", name), self.host.live)

        def interrupt(argv):
            raise KeyboardInterrupt

        self.host.run_live = interrupt
        self.assertEqual(self.run_cli("logs", self.profile.name), 0)
        self.assertIn("Closed the log. The server keeps running.", self.out.getvalue())
        self.assertIn(name, self.host.containers)

    def test_a_missing_model_file_is_reported_with_the_repair_command(self):
        Path(self.profile.model.parts[0]).unlink()
        self.assertEqual(self.run_cli("launch", "--here"), 1)
        text = self.out.getvalue()
        self.assertIn("is missing", text)
        self.assertIn(f"setup --repair {self.profile.name}", text)

    def test_a_missing_model_server_is_downloaded_after_asking(self):
        from ornith_launcher.installed import load_installed

        self.host.images.clear()
        self.app.read_line = lambda prompt: "n"
        self.assertEqual(self.run_cli("launch", "--here"), 1)
        self.assertIn(f"setup --repair {self.profile.name}", self.app.err.getvalue())
        self.assertNotIn(("docker", "pull", self.profile.runtime.image), self.host.live)
        self.app.read_line = lambda prompt: ""  # Enter: yes
        with mock.patch.object(launch.Monitor, "run", return_value=False):
            self.assertEqual(self.run_cli("launch", "--here"), 0)
        self.assertIn(("docker", "pull", self.profile.runtime.image), self.host.live)
        self.assertEqual(load_installed(self.app.paths).images, [self.profile.runtime.image])

    def test_another_profile_is_stopped_first(self):
        other = profile(self.root, name="other", port=18401)
        save_profile(self.app.paths, other)
        DockerRuntime(self.host, self.http).start(other)
        with mock.patch.object(launch.Monitor, "run", return_value=False):
            self.run_cli("launch", self.profile.name, "--here")
        self.assertNotIn(rt.container_name("other"), self.host.containers)
        self.assertIn("Another Ornith server is running (other)", self.out.getvalue())

    def test_failed_start_leaves_nothing_behind(self):
        self.http.health = [503]
        name = rt.container_name(self.profile.name)

        def dies(*args, **kwargs):
            self.host.containers[name]["State"]["Status"] = "exited"
            return original(*args, **kwargs)

        original = DockerRuntime.wait_ready
        with mock.patch.object(DockerRuntime, "wait_ready", dies):
            self.assertEqual(self.run_cli("launch", "--here"), 1)
        self.assertNotIn(name, self.host.containers)

    def test_stop_status_list(self):
        DockerRuntime(self.host, self.http).start(self.profile)
        self.run_cli("status")
        self.assertIn("running", self.out.getvalue())
        self.assertIn("Models folder:", self.out.getvalue())
        self.run_cli("list")
        self.assertIn("Ornith 1.5 35B-A3B Q8_0", self.out.getvalue())
        self.run_cli("stop")
        self.assertEqual(self.host.containers, {})
        self.assertIn("memory released", self.out.getvalue())

    def test_remove_never_deletes_an_external_model(self):
        save_settings(self.app.paths, Settings(default_profile=self.profile.name))
        model = Path(self.profile.model.parts[0])
        self.assertEqual(self.run_cli("remove", self.profile.name, "--yes"), 0)
        self.assertTrue(model.exists())
        self.assertEqual(list_profiles(self.app.paths), [])
        self.assertIn("stays where it is", self.out.getvalue())

    def test_remove_asks_before_deleting_a_download(self):
        self.profile.model.location = "managed"
        shortcut = self.root / "s.desktop"
        shortcut.write_text("x")
        self.profile.integrations["shortcut"] = str(shortcut)
        save_profile(self.app.paths, self.profile)
        model = Path(self.profile.model.parts[0])
        self.app.read_line = lambda prompt: "y"
        self.run_cli("remove", self.profile.name)
        self.assertFalse(model.exists())
        self.assertFalse(shortcut.exists())

    def test_no_command_opens_setup_and_never_starts_a_server(self):
        from ornith_launcher import wizard

        save_settings(self.app.paths, Settings(default_profile=self.profile.name))
        with mock.patch.object(wizard, "run_setup", return_value=0) as setup:
            with mock.patch.object(launch, "run_launch") as run:
                self.assertEqual(self.run_cli(), 0)
        setup.assert_called_once()
        run.assert_not_called()


class SetupProfileTest(unittest.TestCase):
    """The last setup steps, after the wizard has a selection."""

    def setUp(self):
        from tests.test_wizard import WizardTest

        self.case = WizardTest("test_fresh_machine_browses_and_downloads")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def prepare(self, *answers):
        """A downloaded selection on a Docker host, and a prompter that gives ``answers``."""
        from ornith_launcher.ui.prompts import PlainPrompter

        case = self.case
        wizard = case.wizard(["", "d", "", "", "", "", ""])
        selection = wizard.run()
        host = FakeDocker(env=dict(case.app.host.env))
        host.files.update(case.app.host.files)
        host.programs.update(case.app.host.programs)
        host.images.clear()
        case.app.host = host
        replies = iter(answers)

        def read_line(prompt):
            try:
                return next(replies)
            except StopIteration:
                raise EOFError from None

        prompter = PlainPrompter(read_line=read_line, out=case.out)
        return wizard, selection, host, prompter

    def test_builds_saves_and_offers_to_launch(self):
        from ornith_launcher import setup_profile

        case = self.case
        # no coding tool, first terminal, shortcut yes, do not launch now
        wizard, selection, host, prompter = self.prepare("1", "", "y", "n")
        with mock.patch.object(
            setup_profile, "make_runtime", lambda app: DockerRuntime(host, FakeHttp())
        ):
            saved = setup_profile.finish_setup(
                case.app, prompter, selection, wizard.plan, wizard.settings
            )
            self.assertEqual(setup_profile.launch_now(case.app, prompter, saved), 0)
        self.assertEqual(saved.name, "ornith-1.5-35b-q8-262k")
        self.assertEqual(saved.model.location, "managed")
        self.assertEqual(saved.runtime.alias, "ornith-1.5-35b-q8_0")
        self.assertTrue(saved.runtime.direct_io)  # unified memory
        self.assertEqual(saved.plan.fitcheck["CUDA0"], [34667, 5182, 757])
        self.assertIn(("docker", "pull", saved.runtime.image), host.live)
        from ornith_launcher.installed import load_installed

        installed = load_installed(case.app.paths)
        self.assertEqual(installed.images, [saved.runtime.image])  # pulled here, so ours
        self.assertEqual(installed.shortcuts, [saved.integrations["shortcut"]])
        self.assertEqual(saved.terminal, {"id": "this"})
        self.assertIn("Terminal=true", Path(saved.integrations["shortcut"]).read_text())
        self.assertEqual(load_profile(case.app.paths, saved.name), saved)
        from ornith_launcher.settings import load_settings

        self.assertEqual(load_settings(case.app.paths).default_profile, saved.name)
        self.assertEqual(IMAGE.split(":")[0], saved.runtime.image.split(":")[0])

    def test_desktop_icon_by_default_and_the_menu_only_when_asked(self):
        from ornith_launcher import setup_profile
        from ornith_launcher.uninstall import remove_created

        case = self.case
        (case.home / "Desktop").mkdir()
        # no coding tool, first terminal, desktop icon (default yes), menu (default no)
        wizard, selection, host, prompter = self.prepare("1", "", "", "")
        icon = case.home / "Desktop" / "ornith-launcher-ornith-1.5-35b-q8-262k.desktop"
        host.programs["gio"] = "/usr/bin/gio"
        trust = ("gio", "set", str(icon), "metadata::trusted", "true")
        host.commands[trust] = CommandResult(0, "", "")
        with mock.patch.object(
            setup_profile, "make_runtime", lambda app: DockerRuntime(host, FakeHttp())
        ):
            saved = setup_profile.finish_setup(
                case.app, prompter, selection, wizard.plan, wizard.settings
            )
        self.assertEqual(saved.integrations["desktop_copy"], str(icon))
        self.assertTrue(icon.exists())
        self.assertNotIn("shortcut", saved.integrations)  # the applications menu was not asked for
        self.assertIn(trust, host.calls)
        from ornith_launcher.installed import load_installed

        self.assertEqual(load_installed(case.app.paths).shortcuts, [str(icon)])
        self.assertNotIn("Allow Launching", case.out.getvalue())
        remove_created(case.app, saved)
        self.assertFalse(icon.exists())

    def test_the_chat_log_is_offered_with_a_terminal_app_and_saved(self):
        from ornith_launcher import setup_profile

        case = self.case
        (case.home / "Desktop").mkdir()
        # no coding tool, Konsole, chat log yes, desktop icon (default yes), menu (default no)
        wizard, selection, host, prompter = self.prepare("1", "", "y", "", "")
        host.programs["konsole"] = "/usr/bin/konsole"
        with mock.patch.object(
            setup_profile, "make_runtime", lambda app: DockerRuntime(host, FakeHttp())
        ):
            saved = setup_profile.finish_setup(
                case.app, prompter, selection, wizard.plan, wizard.settings
            )
        self.assertEqual(saved.terminal["id"], "konsole")
        self.assertTrue(saved.terminal["chat_log"])
        self.assertIn("Nothing is saved to disk.", case.out.getvalue())
        icon = Path(saved.integrations["desktop_copy"]).read_text()
        self.assertIn("Actions=chat-log;", icon)
        self.assertIn(f"chat-log {saved.name} --window", icon)

    def test_without_gio_the_user_is_told_how_to_allow_the_icon(self):
        from ornith_launcher import setup_profile

        case = self.case
        (case.home / "Desktop").mkdir()
        # no coding tool, first terminal, desktop icon yes, menu yes
        wizard, selection, host, prompter = self.prepare("1", "", "y", "y")
        with mock.patch.object(
            setup_profile, "make_runtime", lambda app: DockerRuntime(host, FakeHttp())
        ):
            saved = setup_profile.finish_setup(
                case.app, prompter, selection, wizard.plan, wizard.settings
            )
        self.assertIn("Allow Launching", case.out.getvalue())
        self.assertTrue(Path(saved.integrations["shortcut"]).exists())
        self.assertTrue(Path(saved.integrations["desktop_copy"]).exists())

    def test_an_image_already_on_the_machine_is_not_recorded(self):
        from ornith_launcher import setup_profile
        from ornith_launcher.installed import load_installed

        host = FakeDocker(env={"HOME": str(self.case.home)})  # already has the image
        setup_profile.ensure_image(self.case.app, DockerRuntime(host, FakeHttp()), IMAGE)
        self.assertEqual(load_installed(self.case.app.paths).images, [])

    def test_quitting_after_the_integrations_undoes_them(self):
        import json

        from ornith_launcher import setup_profile
        from ornith_launcher.ui.prompts import Cancelled

        case = self.case
        # both coding tools, then quit at the terminal question
        wizard, selection, host, prompter = self.prepare("4")
        host.programs.update({"claude": "/usr/bin/claude", "opencode": "/usr/bin/opencode"})
        opencode_file = case.home / ".config" / "opencode" / "opencode.json"
        opencode_file.parent.mkdir(parents=True)
        original = json.dumps({"provider": {"lmstudio": {"name": "LM"}}})
        opencode_file.write_text(original)
        with mock.patch.object(
            setup_profile, "make_runtime", lambda app: DockerRuntime(host, FakeHttp())
        ):
            with self.assertRaises(Cancelled):
                setup_profile.finish_setup(
                    case.app, prompter, selection, wizard.plan, wizard.settings
                )
        self.assertIn("Added ornith-1.5-35b-q8_0", case.out.getvalue())
        self.assertEqual(opencode_file.read_text(), original)
        self.assertEqual(list(opencode_file.parent.iterdir()), [opencode_file])  # no backup left
        bin_dir = case.home / ".local" / "bin"
        self.assertEqual(list(bin_dir.iterdir()) if bin_dir.exists() else [], [])
        self.assertEqual(list_profiles(case.app.paths), [])


if __name__ == "__main__":
    unittest.main()


class DoctorTest(unittest.TestCase):
    def test_reports_problems_with_fixes_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = FakeDocker(env={"HOME": str(root)})
            out = io.StringIO()
            app = App(
                host=host,
                paths=resolve_paths({}, root, "linux"),
                out=out,
                os_name="linux",
                machine="aarch64",
            )
            p = profile(root)
            save_profile(app.paths, p)
            Path(p.model.parts[0]).unlink()
            with mock.patch("ornith_launcher.catalog.catalog._load_seed", return_value=None):
                code = cli.main(["doctor"], app=app)
            text = out.getvalue()
            self.assertEqual(code, 1)
            self.assertIn("Catalog: empty. Fix: ornith-launcher scan --refresh", text)
            self.assertIn(f"Profile {p.name}: 1 problem(s)", text)
            self.assertIn("setup --repair", text)
            self.assertEqual(host.live, [])
            self.assertFalse(
                [c for c in host.calls if c[:2] in (("docker", "run"), ("docker", "rm"))]
            )
