from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ornith_launcher.app import App
from ornith_launcher.bench import needle, report
from ornith_launcher.bench import run as bench
from ornith_launcher.bench.corpus import build_corpus, is_prose, paragraphs
from ornith_launcher.bench.perplexity import parse_perplexity, perplexity_args
from ornith_launcher.config import resolve_paths
from ornith_launcher.planner.rank import TESTED_FILE
from ornith_launcher.profiles import save_profile
from ornith_launcher.resources import load_json
from ornith_launcher.runtime.docker import DockerRuntime
from tests.runtime_fixtures import FakeDocker, FakeHttp, profile

ROOT = Path(__file__).resolve().parent.parent
LONG = "word " * 60


class NeedleTest(unittest.TestCase):
    def test_codes_match_the_dgx_spark_run(self):
        # Same seed as the prototype, so new results compare with the 2026-10-12 run.
        codes = [n.code for n in needle.make_needles()]
        self.assertEqual(codes[:3], ["OCHRE-8629-T", "SLATE-4111-M", "SLATE-8794-Z"])

    def test_document_places_needles_in_order(self):
        paras = [f"paragraph {i} " + LONG for i in range(2000)]
        document = needle.make_document(paras, chars_per_token=4.0, max_tokens=100_000)
        positions = [document.positions[n.vault] for n in document.needles]
        self.assertEqual(positions, sorted(positions))
        prefix, inside = document.prefix(25_000)
        self.assertEqual([n.vault for n in inside], [1, 2])
        self.assertTrue(prefix.endswith(LONG.strip()) or prefix.endswith(LONG))
        self.assertIn(document.needles[0].text, prefix)

    def test_scoring(self):
        inside = needle.make_needles()[:2]
        answer = f"vault 1: {inside[0].code}\nvault 2: `TEAL-0000-K`\nvault 5: AMBER-1234-X"
        result = needle.score(answer, inside)
        self.assertEqual(result["score"], "1/2")
        self.assertEqual(result["wrong_or_invented"], {2: "TEAL-0000-K", 5: "AMBER-1234-X"})

    def test_run_length_records_timings_and_errors(self):
        document = needle.make_document([LONG] * 500, 4.0, max_tokens=10_000)

        def post(path, body):
            self.assertEqual(body["temperature"], 0)
            return {
                "choices": [{"message": {"content": "vault 1: " + document.needles[0].code}}],
                "timings": {"prompt_n": 900, "prompt_ms": 2000, "prompt_per_second": 450.0},
                "usage": {"prompt_tokens": 900},
            }

        result = needle.run_length(post, "m", document, 5_000)
        self.assertEqual(result["prefill_tok_s"], 450.0)
        self.assertEqual(result["prompt_s"], 2.0)

        def broken(path, body):
            raise TimeoutError("slow")

        self.assertIn("TimeoutError", needle.run_length(broken, "m", document, 5_000)["error"])


class GuardTest(unittest.TestCase):
    def test_pauses_when_hot_until_cool(self):
        readings = iter([85.0, 75.0, 66.0, 59.0])
        slept = []
        waited = bench.wait_until_cool(lambda: next(readings), slept.append, lambda s: None)
        self.assertEqual(waited, 90.0)
        self.assertEqual(len(slept), 3)
        self.assertEqual(bench.wait_until_cool(lambda: 70.0, slept.append, print), 0.0)
        self.assertEqual(bench.wait_until_cool(lambda: None, slept.append, print), 0.0)


class PerplexityAndCorpusTest(unittest.TestCase):
    def test_args_and_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = profile(Path(tmp))
            args = perplexity_args(p, Path(tmp) / "ppl.txt", yarn_factor=2)
        self.assertEqual(args[:3], ["docker", "run", "--rm"])
        self.assertIn("perplexity", args)
        self.assertIn("--rope-scale", args)
        self.assertEqual(args[args.index("-c") + 1], "4096")
        log = "[39]6.5790\nFinal estimate: PPL = 6.5790 +/- 0.05912\n"
        self.assertEqual(parse_perplexity(log), (6.5790, 0.05912))
        self.assertIsNone(parse_perplexity("out of memory"))

    def test_corpus_from_a_doc_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "doc"
            (source / "pkg").mkdir(parents=True)
            (source / "pkg" / "README").write_text(f"{LONG}\n\n{LONG}\n\nshort\n")
            (source / "pkg" / "changelog").write_text(f"x-1 {LONG}")
            (source / "pkg" / "secrets").write_text(f"the vault is here {LONG}")
            corpus = build_corpus(Path(tmp) / "out", source)
            self.assertEqual(corpus.prose.read_text(), LONG.strip())  # de-duplicated
            haystack = corpus.haystack.read_text()
            self.assertIn("x-1", haystack)
            self.assertNotIn("vault", haystack)
        self.assertEqual(paragraphs("a\n\n" + LONG), [LONG.strip()])
        self.assertFalse(is_prose("12345 " * 50))


class ReportTest(unittest.TestCase):
    def test_benchmarks_md_is_generated_from_the_data(self):
        expected = report.render(load_json(TESTED_FILE))
        committed = (ROOT / "docs" / "BENCHMARKS.md").read_text(encoding="utf-8")
        self.assertEqual(
            committed,
            expected,
            "regenerate: python3 -m ornith_launcher.bench.report docs/BENCHMARKS.md",
        )

    def test_guide_context_table_matches_the_data(self):
        bench_data = load_json(TESTED_FILE)["benchmarks"]

        def cell(key, model, column):
            section = bench_data[key]
            row = next(r for r in section["rows"] if r["model"] == model)
            index = section["columns"].index(column)
            self.assertEqual(row["levels"][index], "TESTED", (key, model, column))
            return row["values"][index]

        memory = [
            cell("memory", f"35B Q8_0, {c}", "Peak memory in use").replace("-", " to ")
            for c in ("native 262k", "524k", "1M")
        ]
        needles = [
            cell("needle", model, column).replace("/", " of ") + f" at {column} tokens"
            for model, column in (
                ("Uncensored, native 262k", "240k"),
                ("Uncensored, 524k (YaRN x2)", "456k"),
                ("Uncensored, 1M (YaRN x4)", "917k"),
            )
        ]
        prefill = [
            cell("prefill", f"Uncensored, {c}", "Cold total") + " tokens"
            for c in ("native", "524k", "1M")
        ]
        expected = [
            "| Peak memory in use | " + " | ".join(memory) + " |",
            "| Retrieval test, codes found | " + " | ".join(needles) + " |",
            "| Cold prefill | " + " | ".join(prefill) + " |",
        ]
        guide = (ROOT / "docs" / "GUIDE.md").read_text(encoding="utf-8")
        lines = guide.splitlines()
        for line in expected:
            self.assertIn(line, lines, "update the guide's long-context table")
        prose = " ".join(guide.split())  # the prose is wrapped at any word
        standard = cell("prefill", "Standard, 1M", "Cold total")
        self.assertIn(f"took {standard} tokens", prose)
        rows = {r["model"]: r["values"] for r in bench_data["perplexity"]["rows"]}
        x4 = max(float(v[2]) / float(v[0]) - 1 for v in rows.values())
        self.assertIn(f"x4 cost at most {x4:.1%}", prose)

    def test_readme_long_context_claim_matches_the_data(self):
        section = load_json(TESTED_FILE)["benchmarks"]["needle"]
        row = next(r for r in section["rows"] if r["model"] == "Uncensored, 1M (YaRN x4)")
        index = section["columns"].index("917k")
        self.assertEqual((row["values"][index], row["levels"][index]), ("6/6", "TESTED"))
        readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
        claim = "found every hidden code in a 917k-token document on a DGX Spark (TESTED"
        self.assertIn(claim, readme)

    def test_every_number_is_labelled(self):
        data = load_json(TESTED_FILE)["benchmarks"]
        for key in ("perplexity", "needle", "prefill", "memory"):
            for row in data[key]["rows"]:
                with self.subTest(key=key, row=row["model"]):
                    for value, level in zip(row["values"], row["levels"]):
                        if value is not None:
                            self.assertIn(level, ("TESTED", "INFERRED", "ESTIMATED"))


class BenchCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.host = FakeDocker(env={"HOME": str(root)})
        self.app = App(
            host=self.host,
            paths=resolve_paths({}, root, "linux"),
            out=io.StringIO(),
            os_name="linux",
            machine="aarch64",
        )
        self.profile = profile(root)
        save_profile(self.app.paths, self.profile)
        corpus = self.app.paths.state / "bench" / "corpus"
        corpus.mkdir(parents=True)
        (corpus / "haystack.txt").write_text("\n\n".join([LONG] * 3000))
        (corpus / "prose.txt").write_text(LONG)
        (corpus / "ppl.txt").write_text(LONG)
        self.http = FakeHttp()
        self.runtime = DockerRuntime(self.host, self.http)
        patcher = mock.patch.object(bench, "make_runtime", lambda app: self.runtime)
        patcher.start()
        self.addCleanup(patcher.stop)

    def args(self, **kwargs):
        values = dict(name=None, needle=False, perplexity=False, lengths="20000,10000")
        values.update(kwargs)
        return argparse.Namespace(**values)

    def results(self):
        path = self.app.paths.state / "bench" / "results.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_needle_needs_a_running_server(self):
        self.http.health = [503]
        with self.assertRaisesRegex(bench.BenchError, "not running"):
            bench.run_bench(self.app, self.args(needle=True))

    def test_needle_runs_shortest_first_and_records_everything(self):
        original = self.http.post_json

        def post(url, payload, timeout=300.0):
            if url.endswith("/tokenize"):
                from ornith_launcher.runtime.http import Reply

                return Reply(200, json.dumps({"tokens": [0] * 5000}).encode())
            return original(url, payload, timeout)

        self.http.post_json = post
        bench.run_bench(self.app, self.args(needle=True), sleep=lambda s: None)
        rows = self.results()
        self.assertEqual([r["target_tokens"] for r in rows], [10000, 20000])
        self.assertEqual(rows[0]["llama_cpp"], "b11552")
        self.assertEqual(rows[0]["profile"], self.profile.name)

    def test_perplexity_refuses_while_the_server_runs(self):
        self.runtime.start(self.profile)
        with self.assertRaisesRegex(bench.BenchError, "compete for memory"):
            bench.run_bench(self.app, self.args(perplexity=True))
        self.runtime.stop(self.profile)
        self.host.fit_output = "Final estimate: PPL = 6.5790 +/- 0.05912\n"
        bench.run_bench(self.app, self.args(perplexity=True), sleep=lambda s: None)
        self.assertEqual(self.results()[0]["ppl"], 6.579)

    def test_lengths_beyond_the_context_are_refused(self):
        with self.assertRaisesRegex(bench.BenchError, "longer than the profile's context"):
            bench.run_bench(self.app, self.args(needle=True, lengths="300000"))


if __name__ == "__main__":
    unittest.main()
