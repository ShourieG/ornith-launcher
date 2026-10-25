from __future__ import annotations

import http.server
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ornith_launcher.app import App
from ornith_launcher.catalog.gguf import ValueType
from ornith_launcher.config import resolve_paths
from ornith_launcher.integrations import claude_code, installers, opencode
from ornith_launcher.integrations import setup as integrations
from ornith_launcher.system.host import CommandResult
from ornith_launcher.templates import compat
from ornith_launcher.ui.prompts import PlainPrompter
from tests.gguf_builder import gguf_bytes, ornith_pairs
from tests.runtime_fixtures import FakeDocker, profile

SH = shutil.which("sh")


class ModelsHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps(
            {"data": [{"id": "ornith-1.5-35b-q8_0", "meta": {"n_ctx": 262144}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ClaudeWrapperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def test_timeout_grows_with_context(self):
        self.assertEqual(claude_code.api_timeout_ms(65536), 600_000)
        self.assertEqual(claude_code.api_timeout_ms(262144), 600_000)
        self.assertEqual(claude_code.api_timeout_ms(524288), 975_000)
        self.assertEqual(claude_code.api_timeout_ms(1048576), 3_600_000)

    @unittest.skipUnless(SH and os.name != "nt", "needs a POSIX shell")
    def test_wrapper_exports_the_local_server_and_runs_claude(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ModelsHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        p = profile(self.home, port=server.server_address[1])
        wrapper = claude_code.write_wrapper(self.home, p)
        self.assertEqual(wrapper.stat().st_mode & 0o777, 0o755)
        self.assertEqual(subprocess.run([SH, "-n", str(wrapper)]).returncode, 0)
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        fake_claude = bin_dir / "claude"
        fake_claude.write_text('#!/bin/sh\nenv | sort\necho args: "$@"\n')
        fake_claude.chmod(0o755)
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(self.home)}
        result = subprocess.run(
            [str(wrapper), "--continue"], capture_output=True, text=True, env=env, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn(f"ANTHROPIC_BASE_URL=http://127.0.0.1:{p.runtime.port}", out)
        self.assertIn("ANTHROPIC_AUTH_TOKEN=local", out)
        self.assertIn("ANTHROPIC_DEFAULT_FABLE_MODEL=ornith-1.5-35b-q8_0", out)
        self.assertIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS=262144", out)
        self.assertIn("API_TIMEOUT_MS=600000", out)
        self.assertIn("args: --continue", out)

    @unittest.skipUnless(SH and os.name != "nt", "needs a POSIX shell")
    def test_wrapper_explains_a_stopped_server(self):
        p = profile(self.home, port=1)  # nothing listens on port 1
        wrapper = claude_code.write_wrapper(self.home, p)
        result = subprocess.run([str(wrapper)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"Start it with: ornith-launcher launch {p.name}", result.stderr)

    def test_alias_follows_the_default_and_leaves_foreign_files(self):
        first = claude_code.write_wrapper(self.home, profile(self.home, name="a"))
        second = claude_code.write_wrapper(self.home, profile(self.home, name="b"))
        link = claude_code.point_alias(self.home, first)
        self.assertEqual(link.resolve(), first.resolve())
        claude_code.point_alias(self.home, second)
        self.assertEqual(link.resolve(), second.resolve())
        # Removing profile a must not take the alias that now belongs to b.
        integrations.remove_claude_files({"wrapper": str(first), "alias": str(link)})
        self.assertTrue(link.is_symlink())
        self.assertFalse(first.exists())
        link.unlink()
        link.write_text("someone else's script")
        self.assertIsNone(claude_code.point_alias(self.home, second))


class OpenCodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profile = profile(Path(self.tmp.name))
        self.existing = {
            "$schema": opencode.SCHEMA,
            "model": "spark/qwen",
            "provider": {
                "spark": {"npm": "@ai-sdk/openai-compatible", "options": {"baseURL": "x"}},
            },
        }

    def test_merge_keeps_other_keys_and_is_idempotent(self):
        once = opencode.merged(self.existing, self.profile, "ornith-launcher", "Ornith 1.5")
        twice = opencode.merged(once, self.profile, "ornith-launcher", "Ornith 1.5")
        self.assertEqual(once, twice)
        self.assertEqual(once["provider"]["spark"], self.existing["provider"]["spark"])
        self.assertEqual(once["model"], "spark/qwen")
        ours = once["provider"]["ornith-launcher"]
        self.assertEqual(ours["options"]["baseURL"], "http://127.0.0.1:18400/v1")
        self.assertEqual(ours["models"]["ornith-1.5-35b-q8_0"]["limit"]["context"], 262144)
        self.assertEqual(
            opencode.without(once, "ornith-launcher", "ornith-1.5-35b-q8_0"), self.existing
        )

    def test_a_different_port_gets_its_own_provider(self):
        config = opencode.merged({}, self.profile, "ornith-launcher", "x")
        other = profile(Path(self.tmp.name), name="other", port=18401)
        self.assertEqual(opencode.provider_id_for(config, self.profile), "ornith-launcher")
        self.assertEqual(opencode.provider_id_for(config, other), "ornith-launcher-other")

    def test_comment_detection_ignores_strings(self):
        self.assertTrue(opencode.has_comments('{\n  // note\n  "a": 1\n}'))
        self.assertTrue(opencode.has_comments('{"a": 1 /* x */}'))
        self.assertFalse(opencode.has_comments('{"url": "http://x//y"}'))
        stripped = integrations._strip_comments('{\n  // note\n  "u": "a//b", /* c */ "n": 1\n}')
        self.assertEqual(json.loads(stripped), {"u": "a//b", "n": 1})


class ConfigureTest(unittest.TestCase):
    """Screen 5 end to end, with fake tools and files in a temporary home."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.host = FakeDocker(env={"HOME": str(self.home), "PATH": "/usr/bin"})
        self.out = io.StringIO()
        self.app = App(
            host=self.host,
            paths=resolve_paths({}, self.home, "linux"),
            out=self.out,
            os_name="linux",
            machine="aarch64",
        )
        self.profile = profile(self.home)
        rule = compat.RULES[0]
        template = "{%- for message in messages %}\n" + rule.find + "\n{%- endfor %}\n"
        pairs = ornith_pairs("35B") + [("tokenizer.chat_template", ValueType.STRING, template)]
        Path(self.profile.model.path).write_bytes(gguf_bytes(pairs))
        self.opencode_file = self.home / ".config" / "opencode" / "opencode.json"
        self.opencode_file.parent.mkdir(parents=True)
        self.opencode_file.write_text(json.dumps({"provider": {"lmstudio": {"name": "LM"}}}))
        self.host.programs.update({"claude": "/usr/bin/claude", "opencode": "/usr/bin/opencode"})

    def prompter(self, *answers):
        replies = iter(answers)
        return PlainPrompter(read_line=lambda p: next(replies), out=self.out)

    def test_both_tools(self):
        integrations.configure(self.app, self.prompter(), self.profile, integrations.BOTH)
        template = self.profile.runtime.template
        self.assertEqual(template.source, "patched")
        self.assertEqual(template.rule, "late-system-raises")
        patched = Path(template.file).read_text()
        self.assertNotIn("raise_exception", patched)
        self.assertTrue(Path(template.file).is_relative_to(self.app.paths.templates))
        wrapper = Path(self.profile.integrations["claude_code"]["wrapper"])
        self.assertTrue(wrapper.exists())
        self.assertTrue((self.home / ".local/bin/ornith-claude").is_symlink())
        config = json.loads(self.opencode_file.read_text())
        self.assertIn("lmstudio", config["provider"])
        self.assertIn("ornith-launcher", config["provider"])
        backup = Path(self.profile.integrations["opencode"]["backup"])
        self.assertEqual(json.loads(backup.read_text()), {"provider": {"lmstudio": {"name": "LM"}}})
        self.assertIn(claude_code.ANTHROPIC_NOTICE.split(":")[0], claude_code.ANTHROPIC_NOTICE)

    def test_switching_to_opencode_only_undoes_claude(self):
        integrations.configure(self.app, self.prompter(), self.profile, integrations.BOTH)
        patched = Path(self.profile.runtime.template.file)
        integrations.configure(self.app, self.prompter(), self.profile, integrations.OPENCODE_ONLY)
        self.assertNotIn("claude_code", self.profile.integrations)
        self.assertEqual(self.profile.runtime.template.source, "gguf")
        self.assertFalse(patched.exists())
        integrations.configure(self.app, self.prompter(), self.profile, integrations.NONE)
        config = json.loads(self.opencode_file.read_text())
        self.assertNotIn("ornith-launcher", config["provider"])

    def test_jsonc_gets_a_snippet_unless_the_user_agrees(self):
        self.opencode_file.write_text('{\n  // mine\n  "provider": {}\n}\n')
        integrations.configure(
            self.app, self.prompter("n"), self.profile, integrations.OPENCODE_ONLY
        )
        self.assertIn("// mine", self.opencode_file.read_text())
        self.assertIn('"ornith-launcher"', self.out.getvalue())
        integrations.configure(
            self.app, self.prompter("y"), self.profile, integrations.OPENCODE_ONLY
        )
        self.assertIn("ornith-launcher", json.loads(self.opencode_file.read_text())["provider"])

    def test_missing_tool_is_installed_only_with_consent(self):
        del self.host.programs["claude"]
        integrations.configure(self.app, self.prompter("n"), self.profile, integrations.CLAUDE_ONLY)
        self.assertNotIn("claude_code", self.profile.integrations)
        self.assertIn("curl -fsSL https://claude.ai/install.sh | bash", self.out.getvalue())
        self.assertEqual(self.host.live, [])

        def installer_puts_claude(argv):
            self.host.programs["claude"] = "/usr/bin/claude"
            return 0

        self.host.run_live = installer_puts_claude
        self.host.commands[("/usr/bin/claude", "--version")] = CommandResult(0, "2.1\n", "")
        with mock.patch.object(installers, "fetch_script", return_value=b"echo install\n"):
            integrations.configure(
                self.app, self.prompter("y"), self.profile, integrations.CLAUDE_ONLY
            )
        self.assertIn("claude_code", self.profile.integrations)


if __name__ == "__main__":
    unittest.main()
