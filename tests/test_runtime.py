from __future__ import annotations

import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ornith_launcher.profiles import TemplateInfo
from ornith_launcher.runtime import docker as rt
from ornith_launcher.runtime.docker import (
    DockerRuntime,
    ServerError,
    fit_check_args,
    mounts,
    parse_fit_params,
    run_args,
)
from ornith_launcher.runtime.monitor import Monitor, ask_to_stop, status_line
from ornith_launcher.runtime.server import ServerConfig, fit_params_args, server_args
from tests.runtime_fixtures import IMAGE, FakeDocker, FakeHttp, profile


class ServerArgsTest(unittest.TestCase):
    def config(self, **kwargs) -> ServerConfig:
        values = dict(model="/models/m.gguf", alias="a", context=262144, slots=4)
        values.update(kwargs)
        return ServerConfig(**values)

    def test_tested_form(self):
        args = server_args(self.config(sampling={"temperature": 0.6, "top_k": 20}))
        expected_start = ["-m", "/models/m.gguf", "--alias", "a", "--host", "0.0.0.0"]
        self.assertEqual(args[:6], expected_start)
        for flag in ("--kv-unified", "--jinja", "--metrics", "--load-mode", "--fit"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--fit") + 1], "off")
        self.assertEqual(args[args.index("--temp") + 1], "0.6")
        self.assertEqual(args[args.index("--top-k") + 1], "20")
        self.assertNotIn("--rope-scaling", args)
        self.assertNotIn("--chat-template-file", args)

    def test_yarn_only_beyond_native(self):
        args = server_args(self.config(context=1048576, yarn_factor=4))
        index = args.index("--rope-scaling")
        self.assertEqual(
            args[index : index + 6],
            ["--rope-scaling", "yarn", "--rope-scale", "4", "--yarn-orig-ctx", "262144"],
        )

    def test_options(self):
        args = server_args(
            self.config(
                direct_io=False, cpu_moe=True, mmproj="/models/p.gguf", template_file="/t/x.jinja"
            )
        )
        self.assertNotIn("--load-mode", args)
        self.assertIn("--cpu-moe", args)
        self.assertEqual(args[args.index("--mmproj") + 1], "/models/p.gguf")
        self.assertEqual(args[args.index("--chat-template-file") + 1], "/t/x.jinja")

    def test_fit_params_never_gets_kv_unified(self):
        args = fit_params_args(self.config(kv_type="q8_0", context=524288, yarn_factor=2))
        self.assertEqual(args[0], "fit-params")
        self.assertNotIn("--kv-unified", args)
        self.assertIn("q8_0", args)
        self.assertEqual(args[-2:], ["--fit-print", "on"])


class DockerArgsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_run_args_bind_localhost_and_mount_files_read_only(self):
        p = profile(self.root)
        args = run_args(p)
        self.assertEqual(args[:5], ["docker", "run", "-d", "--name", f"ornith-launcher-{p.name}"])
        self.assertIn("127.0.0.1:18400:8080", args)
        model = p.model.parts[0]
        self.assertIn(f"{model}:/models/Ornith-1.5-35B-Q8_0.gguf:ro", args)
        self.assertIn(IMAGE, args)
        self.assertIn("on-failure:3", args)
        self.assertNotIn("--security-opt", args)
        self.assertEqual(args[args.index("-m") + 1], "/models/Ornith-1.5-35B-Q8_0.gguf")

    def test_hash_label_changes_with_settings(self):
        p = profile(self.root)
        first = rt.args_hash(run_args(p))
        self.assertEqual(first, rt.args_hash(run_args(p)))
        bigger = replace(p, runtime=replace(p.runtime, context=524288, yarn_factor=2))
        self.assertNotEqual(first, rt.args_hash(run_args(bigger)))

    def test_split_parts_projector_template_and_selinux(self):
        p = profile(self.root)
        parts = [str(self.root / f"M-0000{i}-of-00002.gguf") for i in (1, 2)]
        p.model.parts = parts
        p.model.mmproj = str(self.root / "mmproj-F16.gguf")
        p.runtime.template = TemplateInfo(
            source="patched", rule="r", file=str(self.root / "p.jinja")
        )
        p.runtime.selinux = True
        targets = [container for _, container in mounts(p)]
        self.assertEqual(
            targets,
            [
                "/models/M-00001-of-00002.gguf",
                "/models/M-00002-of-00002.gguf",
                "/models/mmproj-F16.gguf",
                "/templates/p.jinja",
            ],
        )
        args = run_args(p)
        self.assertIn("label=disable", args)
        self.assertEqual(args[args.index("--chat-template-file") + 1], "/templates/p.jinja")
        fit = fit_check_args(p)
        self.assertEqual(fit[fit.index("--entrypoint") + 1], "/app/llama")

    def test_parse_fit_params(self):
        result = parse_fit_params("noise\nCUDA0 34667 5182 757\nHost 515 0 272\n")
        self.assertEqual(result.devices["CUDA0"], [34667, 5182, 757])
        self.assertEqual(result.total_mib, 34667 + 5182 + 757 + 515 + 272)
        self.assertIsNone(parse_fit_params("error: something\n"))


class DockerRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host = FakeDocker()
        self.http = FakeHttp()
        self.clock = [0.0]
        self.runtime = DockerRuntime(
            self.host, self.http, sleep=self.advance, clock=lambda: self.clock[0]
        )
        self.profile = profile(Path(self.tmp.name))

    def advance(self, seconds):
        self.clock[0] += seconds

    def test_start_then_reuse_then_refuse_other_settings(self):
        self.assertEqual(self.runtime.start(self.profile), "started")
        self.assertEqual(self.runtime.start(self.profile), "reused")
        changed = replace(self.profile, runtime=replace(self.profile.runtime, slots=1))
        with self.assertRaisesRegex(ServerError, "different settings"):
            self.runtime.start(changed)

    def test_stopped_container_is_replaced(self):
        self.runtime.start(self.profile)
        name = rt.container_name(self.profile.name)
        self.host.containers[name]["State"]["Status"] = "exited"
        self.assertEqual(self.runtime.start(self.profile), "started")
        self.assertIn(("docker", "rm", "-f", name), self.host.calls)

    def test_wait_ready_polls_until_healthy(self):
        self.runtime.start(self.profile)
        self.http.health = [503, 503, 200]
        lines = []
        self.assertEqual(self.runtime.wait_ready(self.profile, lines.append), 6.0)
        self.assertTrue(lines)

    def test_server_dying_while_loading_shows_its_log(self):
        self.runtime.start(self.profile)
        self.http.health = [503]
        self.host.containers[rt.container_name(self.profile.name)]["State"]["Status"] = "exited"
        with self.assertRaisesRegex(ServerError, "stopped while loading"):
            self.runtime.wait_ready(self.profile)

    def test_timeout(self):
        self.runtime.start(self.profile)
        self.http.health = [503]
        with self.assertRaisesRegex(ServerError, "not ready after 10 s"):
            self.runtime.wait_ready(self.profile, timeout=10)

    def test_warm_up_and_verify(self):
        self.runtime.warm_up(self.profile)
        url, payload = self.http.posts[0]
        self.assertTrue(url.endswith("/v1/chat/completions"))
        self.assertEqual(payload["max_tokens"], 16)
        self.assertEqual(self.runtime.verify(self.profile), [])
        self.http.props["default_generation_settings"]["n_ctx"] = 131072
        self.assertIn("context is 131072", self.runtime.verify(self.profile)[0])
        self.http.chat_status = 500
        with self.assertRaisesRegex(ServerError, "warm-up"):
            self.runtime.warm_up(self.profile)

    def test_failed_docker_run(self):
        self.host.run_fails = True
        with self.assertRaisesRegex(ServerError, "boom"):
            self.runtime.start(self.profile)

    def test_other_servers_and_metrics(self):
        self.runtime.start(self.profile)
        self.host.other = "vllm-qwen\tvllm/vllm-openai:latest"
        self.assertEqual(
            self.runtime.other_model_servers(), ["vllm-qwen (vllm/vllm-openai:latest)"]
        )
        self.assertEqual(len(self.runtime.running_profiles()), 1)
        self.assertEqual(self.runtime.requests_in_progress(self.profile), 2)

    def test_fit_check(self):
        self.assertEqual(self.runtime.fit_check(self.profile).devices["Host"], [515, 0, 272])

    def test_pull_failure_says_how_to_retry(self):
        self.host.live_codes[("docker", "pull", IMAGE)] = 1
        with self.assertRaisesRegex(ServerError, "could not pull"):
            self.runtime.pull(IMAGE)


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host = FakeDocker()
        self.host.programs["nvidia-smi"] = "/usr/bin/nvidia-smi"
        self.host.files["/proc/meminfo"] = (
            "MemTotal: 127598608 kB\nMemAvailable: 60000000 kB\n"
            "SwapTotal: 16777212 kB\nSwapFree: 10000000 kB\n"
        )
        self.runtime = DockerRuntime(self.host, FakeHttp())
        self.profile = profile(Path(self.tmp.name))
        self.runtime.start(self.profile)

    def test_status_line_and_warnings(self):
        from ornith_launcher.system.host import CommandResult

        query = (
            "nvidia-smi",
            "--query-gpu=temperature.gpu,temperature.gpu.tlimit,power.draw,utilization.gpu",
            "--format=csv,noheader,nounits",
        )
        self.host.commands[query] = CommandResult(0, "78, 6, 82.5, 95\n", "")
        original = self.host.run

        def run(argv, timeout=10.0):
            if tuple(argv) == query:
                return self.host.commands[query]
            return original(argv, timeout)

        self.host.run = run
        line, warnings = status_line(self.host, self.runtime, self.profile, "12:00:00")
        self.assertIn("GPU 78°C (throttle margin 6°C)", line)
        self.assertIn("requests 2", line)
        self.assertIn("[running]", line)
        self.assertEqual(len(warnings), 2)  # near throttling, and 6.5 GiB in swap

    def test_ctrl_c_answers(self):
        out = io.StringIO()
        self.assertTrue(ask_to_stop(out, lambda t: ""))  # Enter = yes
        self.assertFalse(ask_to_stop(out, lambda t: "n\n"))
        self.assertTrue(ask_to_stop(out, lambda t: None))  # no answer in time
        self.assertIn("no answer in 30 s", out.getvalue())

        def second_ctrl_c(timeout):
            raise KeyboardInterrupt

        self.assertTrue(ask_to_stop(out, second_ctrl_c))

    def test_monitor_stops_the_server_on_ctrl_c(self):
        def interrupt(seconds):
            raise KeyboardInterrupt

        out = io.StringIO()
        monitor = Monitor(
            self.host,
            self.runtime,
            self.profile,
            out=out,
            sleep=interrupt,
            read_answer=lambda t: "",
        )
        self.assertTrue(monitor.run())
        self.assertEqual(self.runtime.status(self.profile).state, "missing")
        self.assertIn("memory is free again", out.getvalue())

    def test_monitor_closes_when_the_server_is_gone(self):
        self.runtime.stop(self.profile)
        out = io.StringIO()
        monitor = Monitor(self.host, self.runtime, self.profile, out=out, sleep=lambda s: None)
        self.assertFalse(monitor.run(max_lines=3))
        self.assertIn("The server is gone", out.getvalue())


if __name__ == "__main__":
    unittest.main()
