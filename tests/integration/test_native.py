"""The native backend on a real Mac: install the pinned build, start, check and stop a server.

Run by the owner on a Mac, never in CI (downloads about 12 MB, then loads a model):

    export ORNITH_IT_MODEL=~/models/Ornith-1.5-9B-Q4_K_M.gguf   # any Ornith GGUF that fits
    ORNITH_IT=1 python3 -m unittest tests.integration.test_native -v

The runtime is unpacked into a temporary folder, so nothing is left in the
launcher's own folders. It also prints what Metal reports, for checking the
memory planner's Metal limit against the real one.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from ornith_launcher.config import resolve_paths
from ornith_launcher.pins import load_pins
from ornith_launcher.profiles import ModelInfo, PlanInfo, Profile, RuntimeInfo
from ornith_launcher.runtime.native import NativeRuntime, runtime_root
from ornith_launcher.system.host import LocalHost
from ornith_launcher.system.report import check_system

MODEL = os.environ.get("ORNITH_IT_MODEL", "")
PORT = 18491


@unittest.skipUnless(
    os.environ.get("ORNITH_IT") == "1" and MODEL and sys.platform == "darwin",
    "on a Mac, set ORNITH_IT=1 and ORNITH_IT_MODEL=<path to an Ornith GGUF> to run",
)
class NativeServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.host = LocalHost()
        self.paths = resolve_paths({}, root, "darwin")
        report = check_system(self.host, load_pins())
        self.assertFalse(report.blocking, [p.message for p in report.blocking])
        self.assertIsNotNone(report.native)
        self.runtime = NativeRuntime(self.host, self.paths)
        self.reference = report.native.reference
        model = Path(MODEL).expanduser().resolve()
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
                image=self.reference,
                flavour="metal" if report.gpus else "cpu",
                device_args=[],
                context=32768,
                native_context=262144,
                slots=1,
                kv_type="f16",
                port=PORT,
                alias="ornith-it",
                direct_io=False,
            ),
            plan=PlanInfo(estimate_gib=0, level="ESTIMATED"),
        )
        self.addCleanup(self.runtime.stop, self.profile)

    def test_install_start_check_and_stop(self):
        self.runtime.pull(self.reference)
        self.assertTrue(self.runtime.image_present(self.reference))
        self.assertFalse(self.runtime.quarantined(self.reference), "Gatekeeper may block it")

        report = check_system(self.host, load_pins(), runtime_root=runtime_root(self.paths))
        for pool in report.pools:
            print(f"\n  pool: {pool.name}, {pool.total / 1024**3:.1f} GiB", file=sys.stderr)
        if report.gpus:
            self.assertIn("Metal limit", report.pools[0].name)
            self.assertNotIn("ESTIMATED", report.pools[0].name, "--list-devices was not parsed")

        fit = self.runtime.fit_check(self.profile)
        self.assertIsNotNone(fit, "llama-fit-params gave no device lines")
        print(f"  fit-params: {fit.devices}", file=sys.stderr)

        self.assertEqual(self.runtime.start(self.profile), "started")
        self.runtime.wait_ready(self.profile, timeout=900)
        self.runtime.warm_up(self.profile)
        self.assertEqual(self.runtime.verify(self.profile), [])
        self.assertEqual(self.runtime.start(self.profile), "reused")

        self.assertTrue(self.runtime.stop(self.profile))
        self.assertEqual(self.runtime.status(self.profile).state, "missing")
        self.assertFalse(self.runtime.run_file(self.profile.name).exists())


if __name__ == "__main__":
    unittest.main()
