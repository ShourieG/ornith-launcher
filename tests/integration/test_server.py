"""Start a real llama.cpp server in Docker with a real model (GPU, minutes, tens of GB).

Run by the owner, never in CI:

    export ORNITH_IT_MODEL=~/models/ornith-ai/Ornith-1.5-35B-A3B-GGUF/Ornith-1.5-35B-Q8_0.gguf
    ORNITH_IT=1 python3 -m unittest tests.integration.test_server -v

Each test starts its own server on a spare port and removes it afterwards.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ornith_launcher.pins import load_pins
from ornith_launcher.profiles import ModelInfo, PlanInfo, Profile, RuntimeInfo, TemplateInfo
from ornith_launcher.runtime.docker import DockerRuntime
from ornith_launcher.system.host import LocalHost
from ornith_launcher.system.report import check_system
from ornith_launcher.templates import compat

MODEL = os.environ.get("ORNITH_IT_MODEL", "")
PORT = 18490
LEADING_ONLY = [
    {"role": "system", "content": "You are terse."},
    {"role": "user", "content": "Say hi."},
]
LATE_SYSTEM = LEADING_ONLY + [
    {"role": "assistant", "content": "Hi."},
    {"role": "system", "content": "From now on answer in French."},
    {"role": "user", "content": "Say hi again."},
]


@unittest.skipUnless(
    os.environ.get("ORNITH_IT") == "1" and MODEL,
    "set ORNITH_IT=1 and ORNITH_IT_MODEL=<path to an Ornith GGUF> to run",
)
class RealServerTest(unittest.TestCase):
    def setUp(self):
        host = LocalHost()
        report = check_system(host, load_pins())
        self.assertFalse(report.blocking, [p.message for p in report.blocking])
        model = Path(MODEL).expanduser().resolve()
        self.runtime = DockerRuntime(host)
        self.profile = Profile(
            name="integration-test",
            created="test",
            launcher_version="test",
            model=ModelInfo(
                path=str(model),
                parts=[str(model)],
                size=model.stat().st_size,
                mtime_ns=model.stat().st_mtime_ns,
                location="external",
            ),
            runtime=RuntimeInfo(
                image=report.flavour.image.reference,
                flavour=report.flavour.image.flavour,
                device_args=list(report.flavour.device_args),
                context=65536,
                native_context=262144,
                slots=1,
                kv_type="f16",
                port=PORT,
                alias="ornith-it",
                selinux=report.docker.selinux,
            ),
            plan=PlanInfo(estimate_gib=0, level="ESTIMATED"),
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.runtime.stop, self.profile)

    def serve(self, profile: Profile) -> None:
        self.runtime.stop(profile)
        self.assertIn(self.runtime.start(profile), ("started", "reused"))
        self.runtime.wait_ready(profile, timeout=900)

    def render(self, profile: Profile, messages) -> tuple[int, str]:
        reply = self.runtime.http.post_json(
            f"{profile.url}/apply-template", {"messages": messages}, timeout=60
        )
        prompt = (reply.json() or {}).get("prompt", "")
        return reply.status, prompt

    def test_launch_warm_up_and_a_claude_code_style_request(self):
        self.serve(self.profile)
        self.runtime.warm_up(self.profile)
        self.assertEqual(self.runtime.verify(self.profile), [])
        reply = self.runtime.http.post_json(
            f"{self.profile.url}/v1/messages",
            {
                "model": "ornith-it",
                "max_tokens": 32,
                "system": "You are terse.",
                "messages": [{"role": "user", "content": "Say hi."}],
            },
            timeout=300,
        )
        self.assertEqual(reply.status, 200, reply.text[:300])

    def test_patched_template_renders_leading_system_messages_identically(self):
        original = compat.read_template(Path(self.profile.model.path))
        self.assertIsNotNone(original, "the model has no chat template")
        verdict = compat.check(original)
        if not verdict.needs_patch:
            self.skipTest("no rule applies to this model's template")
        self.serve(self.profile)
        status, before = self.render(self.profile, LEADING_ONLY)
        self.assertEqual(status, 200)
        late_status, late_before = self.render(self.profile, LATE_SYSTEM)

        patched_file = Path(self.tmp.name) / "patched.jinja"
        patched_file.write_text(compat.apply(original, verdict.rule), encoding="utf-8")
        patched = replace(
            self.profile,
            runtime=replace(
                self.profile.runtime,
                template=TemplateInfo(
                    source="patched", rule=verdict.rule.id, file=str(patched_file)
                ),
            ),
        )
        self.serve(patched)
        status, after = self.render(patched, LEADING_ONLY)
        self.assertEqual(status, 200)
        self.assertEqual(before, after)
        late_status_after, late_after = self.render(patched, LATE_SYSTEM)
        self.assertEqual(late_status_after, 200)
        self.assertIn("From now on answer in French.", late_after)
        if late_status == 200:
            self.assertNotIn("From now on answer in French.", late_before)


if __name__ == "__main__":
    unittest.main()
