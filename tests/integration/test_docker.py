from __future__ import annotations

import os
import shutil
import subprocess
import unittest


@unittest.skipUnless(os.environ.get("ORNITH_IT") == "1", "set ORNITH_IT=1 to run integration tests")
class DockerTest(unittest.TestCase):
    def test_docker_daemon_is_reachable(self):
        docker = shutil.which("docker")
        if docker is None:
            self.fail("docker is not on PATH; install Docker Engine to run integration tests")
        result = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr.strip())
        self.assertTrue(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
