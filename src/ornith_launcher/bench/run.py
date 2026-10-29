"""The ``bench`` command: needle and perplexity tests with a thermal guard.

Long-context runs keep the GPU near 80 °C for half an hour or more. The guard
pauses between steps once the GPU reaches ``HOT`` and waits until it cools to
``COOL``. Every result goes to a JSONL file in the state folder together with
what is needed to compare it later: machine, llama.cpp build, image digest,
model checksum and settings.
"""

from __future__ import annotations

import argparse
import datetime
import json
import platform
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ornith_launcher.app import App
from ornith_launcher.bench import needle
from ornith_launcher.bench.corpus import build_corpus
from ornith_launcher.bench.perplexity import (
    native_perplexity_args,
    parse_perplexity,
    perplexity_args,
)
from ornith_launcher.errors import OrnithLauncherError
from ornith_launcher.launch import make_runtime, resolve_name
from ornith_launcher.pins import load_pins
from ornith_launcher.profiles import Profile, load_profile
from ornith_launcher.runtime.native import PERPLEXITY
from ornith_launcher.system.thermal import read_thermal

HOT = 80.0
COOL = 60.0
POLL = 30.0
DEFAULT_LENGTHS = (120_000, 250_000, 480_000, 950_000)


class BenchError(OrnithLauncherError):
    pass


def wait_until_cool(
    read_temperature: Callable[[], float | None],
    sleep: Callable[[float], None],
    say: Callable[[str], None],
) -> float:
    """Pause while the GPU is at HOT or above, until it reaches COOL; returns seconds waited."""
    temperature = read_temperature()
    if temperature is None or temperature < HOT:
        return 0.0
    say(f"GPU at {temperature:.0f}°C: pausing until it cools to {COOL:.0f}°C.")
    waited = 0.0
    while temperature is not None and temperature > COOL:
        sleep(POLL)
        waited += POLL
        temperature = read_temperature()
    say(f"GPU at {temperature:.0f}°C after {waited / 60:.0f} min; continuing.")
    return waited


def machine_info(app: App) -> dict[str, Any]:
    from ornith_launcher.system.gpu import detect_gpus

    gpus = [g.name for g in detect_gpus(app.host)]
    return {"os": platform.platform(), "gpus": gpus, "python": platform.python_version()}


def record(profile: Profile, app: App) -> dict[str, Any]:
    return {
        "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "profile": profile.name,
        "model": profile.model.repo or profile.model.path,
        "file": Path(profile.model.path).name,
        "model_sha256": profile.model.sha256,
        "llama_cpp": load_pins().build,
        "image": profile.runtime.image,
        "context": profile.runtime.context,
        "kv": profile.runtime.kv_type,
        "slots": profile.runtime.slots,
        "machine": machine_info(app),
    }


def append(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", nargs="?", help="profile name")
    parser.add_argument("--needle", action="store_true", help="long-context retrieval test")
    parser.add_argument("--perplexity", action="store_true", help="short-context quality test")
    parser.add_argument(
        "--lengths",
        default=",".join(str(n) for n in DEFAULT_LENGTHS),
        help="prompt lengths in tokens for --needle, comma-separated",
    )


def run_bench(
    app: App, args: argparse.Namespace, sleep: Callable[[float], None] = time.sleep
) -> int:
    profile = load_profile(app.paths, resolve_name(app, args.name))
    runtime = make_runtime(app)
    results = app.paths.state / "bench" / "results.jsonl"
    corpus = build_corpus(app.paths.state / "bench" / "corpus")
    if not (args.needle or args.perplexity):
        args.needle = True
    base = record(profile, app)

    def temperature() -> float | None:
        thermal = read_thermal(app.host)
        return thermal.temperature if thermal else None

    if args.needle:
        if runtime.http.get(f"{runtime.api_url(profile)}/health").status != 200:
            raise BenchError(
                f"{profile.name} is not running",
                hint=f"Start it first: ornith-launcher launch {profile.name} --no-monitor",
            )
        lengths = sorted(int(x) for x in args.lengths.split(",") if x.strip())
        too_long = [n for n in lengths if n > profile.runtime.context]
        if too_long:
            raise BenchError(
                f"{too_long[0]} tokens is longer than the profile's context "
                f"({profile.runtime.context})",
                hint="Use shorter --lengths, or a profile with a longer context.",
            )
        paragraphs = corpus.haystack.read_text(encoding="utf-8").split("\n\n")

        def post(path: str, body: dict) -> dict:
            reply = runtime.http.post_json(f"{runtime.api_url(profile)}{path}", body, timeout=7200)
            if reply.status != 200:
                raise BenchError(f"HTTP {reply.status}: {reply.text[:200]}")
            return reply.json() or {}

        sample = "\n\n".join(paragraphs[:400])
        tokens = len(post("/tokenize", {"content": sample}).get("tokens", [])) or 1
        document = needle.make_document(paragraphs, len(sample) / tokens)
        for length in lengths:
            wait_until_cool(temperature, sleep, app.say)
            app.say(f"Needle test at {length} tokens...")
            result = needle.run_length(post, profile.runtime.alias, document, length)
            append(results, {**base, "test": "needle", **result})
            app.say(f"  {result['score']} correct, prefill {result['prefill_tok_s']} tokens/s")
    if args.perplexity:
        if runtime.status(profile).alive:
            raise BenchError(
                "the server is running and would compete for memory with the perplexity run",
                hint=f"Stop it first: ornith-launcher stop {profile.name}",
            )
        wait_until_cool(temperature, sleep, app.say)
        app.say("Perplexity at 4096 tokens per chunk (several minutes)...")
        yarn = profile.runtime.yarn_factor
        if runtime.kind == "native":
            binary = runtime.tool(profile.runtime.image, PERPLEXITY)
            argv = native_perplexity_args(binary, profile, corpus.perplexity, yarn)
        else:
            argv = perplexity_args(profile, corpus.perplexity, yarn)
        result = app.host.run(argv, timeout=7200)
        log = (result.stdout + result.stderr) if result else ""
        value = parse_perplexity(log)
        if value is None:
            raise BenchError("llama perplexity did not finish", hint="Run it again with --verbose.")
        append(results, {**base, "test": "perplexity", "ppl": value[0], "error": value[1]})
        app.say(f"  perplexity {value[0]:.4f} +/- {value[1]:.4f}")
    app.say(f"Results appended to {results}")
    return 0
