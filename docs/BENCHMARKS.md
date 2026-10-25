# Benchmarks

Generated from `src/ornith_launcher/data/tested.json` by `python3 -m ornith_launcher.bench.report`;
do not edit by hand.

Every number is labelled: TESTED (measured), INFERRED (derived from measured runs of the
same architecture; the method is stated) or ESTIMATED (calculated only, never run). Results come
from one machine and are no guarantee for others.

## Setup

NVIDIA DGX Spark (GB10, 128 GB unified memory, arm64), Ubuntu, Docker, llama.cpp b11552 (server-cuda13), 2026-10-12. Server: -np 4 --kv-unified -fa on -ngl 999 --load-mode dio, f16 KV cache.

- Standard: `ornith-ai/Ornith-1.5-35B-A3B-GGUF, Ornith-1.5-35B-Q8_0.gguf`
- Uncensored: `dealignai/Ornith-1.5-35B-A3B-UNCENSORED-GGUF, Ornith-1.5-35B-A3B-CRACK-Q8_0.gguf`

Only the 1.5 35B-A3B has been measured. No Ornith 1.0 file, no 9B, no 122B and no 397B has been run, so all their figures are ESTIMATED, and the 397B models are also marked UNTESTED LOCALLY.

## Short-context quality: perplexity

llama perplexity on 640 KB of English prose from the machine's /usr/share/doc README and NEWS files, -c 4096, 39 chunks (about 160k tokens). Lower is better; error +/- 0.059.

| Run | Native | YaRN x2 | YaRN x4 | Level |
|---|---|---|---|---|
| Standard | 6.5790 | 6.5709 | 6.5924 | TESTED |
| Uncensored | 6.6032 | 6.6068 | 6.6275 | TESTED |

YaRN x2 costs nothing measurable; x4 costs at most 0.4%. The uncensored build is about 0.4% worse than the official one at every setting.

## Long-context retrieval: multi-needle

Six vault access codes at 3/20/40/60/80/95% depth of a document of about 917k tokens built from local documentation; prefixes of increasing length; the question asks for every code; temperature 0, thinking off. Score = codes returned exactly / codes inside the prefix. No run returned an invented or wrong code. This test is easy (exact retrieval) and does not show reasoning quality over long context.

| Run | 118k | 240k | 456k | 917k | Level |
|---|---|---|---|---|---|
| Uncensored, native 262k | 1/1 | 2/2 | n/a | n/a | TESTED |
| Uncensored, 524k (YaRN x2) | 1/1 | 2/2 | 3/3 | n/a | TESTED |
| Uncensored, 1M (YaRN x4) | 1/1 | 2/2 | 3/3 | 6/6 | TESTED |
| Standard, 1M (YaRN x4) | 1/1 | 2/2 | 3/3 | 6/6 | TESTED |
| Standard, 524k (YaRN x2) | 1/1 | 2/2 | 3/3 | n/a | INFERRED |
| Standard, native 262k | 1/1 | 2/2 | n/a | n/a | INFERRED |

The standard 524k and native runs were skipped because of GPU heat. They are inferred: same architecture and quant as the uncensored build, which scored perfectly at both settings, and the standard build scored perfectly under the harder YaRN x4 setting at every length.

## Prefill speed

Prompt tokens per second for each new stretch of prompt; the prompt cache reuses earlier stretches.

| Run | 0-118k | 118k-240k | 240k-456k | 456k-917k | Cold total | Level |
|---|---|---|---|---|---|---|
| Uncensored, 1M | 1,449 | 927 | 581 | 338 | 32.5 min to 917k | TESTED |
| Standard, 1M | 1,450 | 905 | 518 | 309 | 35.5 min to 917k | TESTED |
| Uncensored, 524k | 1,277 | 838 | 621 | n/a | 9.8 min to 456k | TESTED |
| Uncensored, native | 1,445 | 964 | n/a | n/a | 3.5 min to 240k | TESTED |
| Standard, 524k | ~1,280 | ~820-840 | ~560-620 | n/a | ~9.8-10.5 min to 456k | INFERRED |
| Standard, native | ~1,445 | ~940-964 | n/a | n/a | ~3.5 min to 240k | INFERRED |

Per-stretch ratio standard/uncensored at 1M was 1.00, 0.98 and 0.89; the lower ratios came with a hotter GPU. Applied as a range to the uncensored 524k and native numbers.

## Memory

Peak system memory in use (MemTotal - MemAvailable) during the runs; the idle baseline was about 5 GiB. llama.cpp fit-params estimates exclude the vision projector.

| Run | Peak memory in use | llama.cpp fit-params estimate | Level |
|---|---|---|---|
| 35B Q8_0, native 262k | 50.9-51.4 GiB (TESTED) | 40.4 GiB (ESTIMATED) | mixed |
| 35B Q8_0, 524k | 56.6-57.4 GiB (TESTED) | ~45.9 GiB (ESTIMATED) | mixed |
| 35B Q8_0, 1M | 67.2-70.0 GiB (TESTED) | 56.5 GiB (ESTIMATED) | mixed |
| 9B Q8_0, native 262k | n/a | 18.0 GiB | ESTIMATED |

The 524k peak was measured with the uncensored build; the standard build is inferred to be the same. Boot took about 9 s and warm-up under 1 s (TESTED).

## Thermals

During long prefills the GPU ran at 75-82 °C (average 77.6 °C over 39 samples), throttle margin 8-18 °C, about 76-85 W and 93-96% load, for more than 30 minutes per 1M run. No throttling failures; the standard 1M run's later stretches were about 10% slower, plausibly from heat (TESTED).

## Not measured yet

- Generation speed at long context
- q8_0 KV cache
- The 9B, any setting
- Any Ornith 1.0 file
- The 122B
- Any 397B (only the 1.5 IQ1_S fits a DGX Spark, ESTIMATED)
- Concurrent requests across 4 slots
- Harder long-context tasks (multi-hop, aggregation, summarisation)
- Repeat runs for variance
- Any machine other than a DGX Spark
- Claude Code end to end with the original template versus the patched one
