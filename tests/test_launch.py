from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ornith_launcher import cli, launch
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
        self.assertEqual(
            shortcut_path("p1", {}, Path("/home/u")),
            Path("/home/u/.local/share/applications/ornith-launcher-p1.desktop"),
        )


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
        self.assertIn("Ready after", text)
        self.assertIn("is ready at http://127.0.0.1:18400", text)
        self.assertTrue(self.http.posts)  # warm-up
        self.assertIn(rt.container_name(self.profile.name), self.host.containers)

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

    def test_missing_file_or_image_is_reported_with_the_repair_command(self):
        Path(self.profile.model.parts[0]).unlink()
        self.host.images.clear()
        self.assertEqual(self.run_cli("launch", "--here"), 1)
        text = self.out.getvalue()
        self.assertIn("is missing", text)
        self.assertIn("is not downloaded", text)
        self.assertIn(f"setup --repair {self.profile.name}", text)

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

    def test_no_command_launches_the_default_profile(self):
        save_settings(self.app.paths, Settings(default_profile=self.profile.name))
        with mock.patch.object(launch, "run_launch", return_value=0) as run:
            self.assertEqual(self.run_cli(), 0)
        self.assertIsNone(run.call_args.args[1].name)


class SetupProfileTest(unittest.TestCase):
    """The last setup steps, after the wizard has a selection."""

    def setUp(self):
        from tests.test_wizard import WizardTest

        self.case = WizardTest("test_fresh_machine_browses_and_downloads")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_builds_saves_and_offers_to_launch(self):
        from ornith_launcher import setup_profile

        case = self.case
        wizard = case.wizard(["", "b", "", "", "", "", ""])
        selection = wizard.run()
        host = FakeDocker(env=dict(case.app.host.env))
        host.files.update(case.app.host.files)
        host.programs.update(case.app.host.programs)
        host.images.clear()
        case.app.host = host
        # no coding tool, first terminal, shortcut yes, do not launch now
        answers = iter(["1", "", "y", "n"])
        case.app.read_line = lambda prompt: next(answers)
        from ornith_launcher.ui.prompts import PlainPrompter

        prompter = PlainPrompter(read_line=case.app.read_line, out=case.out)
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
        self.assertTrue(Path(saved.integrations["shortcut"]).exists())
        self.assertEqual(load_profile(case.app.paths, saved.name), saved)
        from ornith_launcher.settings import load_settings

        self.assertEqual(load_settings(case.app.paths).default_profile, saved.name)
        self.assertEqual(IMAGE.split(":")[0], saved.runtime.image.split(":")[0])


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
