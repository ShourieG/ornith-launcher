# Disclaimer

Version 3 (2026-10-29). ornith-launcher shows this text on first run and asks again whenever the version changes.

ornith-launcher is an unofficial project. It is not affiliated with, endorsed by, or sponsored by Ornith AI,
Anthropic, Anomaly/OpenCode, Hugging Face, Docker, NVIDIA, AMD, Intel or ggml.org.

## No warranty

The software is provided "AS IS", without warranties or conditions of any kind, and the authors are not liable for
any damages arising from its use. See sections 15 and 16 of the GNU General Public License, version 3, in
[LICENSE](LICENSE).

## Models

The models this tool downloads and runs are made by third parties. Their behaviour, accuracy, safety and licences
are the responsibility of the model authors. You must follow each model's licence and the law that applies to you.

Some community models are "uncensored" or "abliterated": their safety behaviour has been removed, and they can
produce harmful or illegal content. ornith-launcher tries to hide them unless you opt in, and it does not endorse,
review or vouch for them. The opt-in is saved: once you show them, they appear in every list and search, in later
runs too, until you hide them again. You alone are responsible for how you use these models and for following the
law where you live.

Hiding and labelling these models is best effort. It is not a content filter, and it is no guarantee of how any
model behaves. The launcher can only go by what uploaders publish on Hugging Face: names, tags and the models a
file was made from. So the label can be wrong in either direction: a model that does not describe itself can appear
in the normal lists without it, and the "only uncensored" list may miss some or include others. Its order (best fit
first, or the memory, size, likes or downloads sort you pick) is not a recommendation. Before you use a community
model, read its Hugging Face model card; every model in the lists links to it. Describing a model accurately is the
responsibility of whoever published it. If you find an uncensored model without the label, please report it at
https://github.com/ShourieG/ornith-launcher/issues so the keyword list can be updated.

## Claude Code with local models

Anthropic does not support this use. From Anthropic's Claude Code documentation: "Anthropic doesn't endorse,
maintain, or audit third-party gateway products, and doesn't support routing Claude Code to non-Claude models
through any gateway." Claude Code's own terms still apply to Claude Code.

## Benchmarks

Benchmark results were measured on one machine and are labelled TESTED, INFERRED or ESTIMATED. They are no
guarantee of results on other systems. Many models in the catalog have never been run by this project. The largest
ones (397B) are marked "untested locally", and their memory and speed figures are estimates.

## Hardware

Long-context runs keep the GPU at high load and temperature for long periods. On a DGX Spark, a 1M-token prompt
held the GPU at about 80 °C for more than 30 minutes (TESTED). Use the tool at your own risk.

## Docker Desktop

This applies only if you run Docker Desktop on Linux instead of Docker Engine; macOS does not use Docker.
Docker Desktop is free for personal use, education, non-commercial open source projects and small businesses
(fewer than 250 employees and less than $10 million in annual revenue). Larger organisations need a paid Docker
subscription. Docker Engine on Linux is not affected.

## Network and privacy

ornith-launcher has no telemetry. It connects only to Hugging Face (models), ghcr.io (llama.cpp Docker images on
Linux), GitHub (the llama.cpp release build on macOS) and, if you choose them, the official Claude Code
and OpenCode installers. The optional chat log shows your requests and the model's replies on your own screen; it
keeps the last few in memory while the server runs and never saves or sends them.
