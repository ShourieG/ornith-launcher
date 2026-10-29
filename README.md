# ornith-launcher

[![CI](https://github.com/ShourieG/ornith-launcher/actions/workflows/ci.yml/badge.svg)](https://github.com/ShourieG/ornith-launcher/actions/workflows/ci.yml)
[![Licence: GPL-3.0-or-later](https://img.shields.io/badge/licence-GPL--3.0--or--later-blue)](LICENSE)

An unofficial tool that runs Ornith models on your own computer with one command. ornith-launcher picks a build
that fits your memory, downloads and checks it, writes the llama.cpp settings, and can connect Claude Code or
OpenCode to the model, showing you every choice and why it made it.

- **Up to 1M tokens of context.** The native 262k, or 524k and 1M with YaRN set up for you. Ornith 1.5 35B-A3B
  found every hidden code in a 917k-token document on a DGX Spark (TESTED, a simple retrieval test);
  [details and costs](docs/GUIDE.md#long-contexts-on-ornith).
- **One-click launch.** A desktop icon starts the model and opens Claude Code or OpenCode next to it. Ctrl+C stops
  it and frees the memory; [details](docs/GUIDE.md#running-a-model).

> Runs on Linux (with Docker) and macOS (without Docker); Windows is planned. Not affiliated with Ornith AI or any
> other company named here; see [NOTICE](NOTICE) and [DISCLAIMER.md](DISCLAIMER.md).

![Setup's best fit list on a MacBook Pro with 32 GB: Ornith files that fit, each with its context, memory, download
size and popularity, and the highlighted file's details and Mac note below](docs/images/best-fit-list-macos.png)

Setup's best fit list on a MacBook Pro with 32 GB: the Ornith files on Hugging Face that fit this Mac, best first,
each at the context and memory setup would use.

## Quick start

```
git clone https://github.com/ShourieG/ornith-launcher.git
cd ornith-launcher
./ornith-launcher
```

You need Python 3.9 or newer. Linux also needs Docker; a Mac needs macOS 13.3 or newer and no Docker.
`./ornith-launcher doctor` checks this and prints the command for anything missing.

Press Enter at each step to take the recommended choice. A summary shows the model, settings, memory and download
size before anything is fetched. At the end, setup asks about Claude Code or OpenCode and a launch icon. Then:

| To | Do this |
|---|---|
| Start the model | Double-click its launch icon, or run `./ornith-launcher launch` |
| Stop it | Press Ctrl+C in its window, or run `./ornith-launcher stop` |
| Use it from Claude Code | `ornith-claude` instead of `claude` |
| Use it from OpenCode | `opencode`, then pick "Ornith (local, ornith-launcher)" under `/models` |
| Add another model | Run `./ornith-launcher` again |
| Remove things | `./ornith-launcher uninstall` |

[docs/GUIDE.md](docs/GUIDE.md) walks through every screen and option.

## Motivation

Running a model locally means getting several things right: a model file that fits your memory, a llama.cpp build
and its flags, a context size, a way to start and stop the server, and settings for each coding tool. A wrong guess
often shows up only after a long download, as a model that will not load.

ornith-launcher works these out with fixed rules. It reads each model's GGUF header, calculates the memory for this
machine and writes the llama.cpp command itself; no chatbot or coding agent writes a config for you. It covers
Ornith models only, and that is what makes fixed rules possible: each size has its own entry with its layer shape
for the memory maths, its native context, whether YaRN is documented for it, and its recommended sampling.

If you are new, pressing Enter through setup gives you a model that runs, and the
[guide](docs/GUIDE.md#new-to-local-models) explains the terms. If you already run models, each model, quant and
context you try is saved as a profile you can launch, compare or remove on its own.

## Environment setup simplification

- **Builds that fit.** Every file is marked fits, stretch or too big for this machine's memory and GPU, with the
  reason. Not sure whether a bigger model at a low quant beats a smaller one at a high quant? The
  [guide](docs/GUIDE.md#choosing-a-size-a-quant-and-a-context) sums up the published evidence.
- **Small machines too.** Contexts from 32k, and a "best for small memory" sort, for when 1M is more than you
  need; [details](docs/GUIDE.md#sorts).
- **Claude Code and OpenCode.** An `ornith-claude` command for Claude Code, and a provider entry for OpenCode.
  Setup offers each tool's official installer if it is missing;
  [details](docs/GUIDE.md#using-the-model-from-other-tools).
- **Models you already have.** Models in the Hugging Face cache, LM Studio, Ollama and `~/models` are used where
  they are, never moved or copied.
- **Checked downloads.** Models and llama.cpp are pinned and verified by sha256 or digest, and a stopped download
  resumes later.
- **A clean uninstall.** It removes only what the launcher created, and lists everything before deleting;
  [details](docs/GUIDE.md#uninstalling).

## Platforms tested

Linux on an NVIDIA DGX Spark (Ubuntu 24.04, Ornith 1.5 35B-A3B), and macOS on a MacBook Pro with an M1 Max and
32 GB (Ornith 1.5 9B). Other GPUs, distributions, Intel Macs and models have not been run yet, and their plans are
ESTIMATED. [docs/HARDWARE.md](docs/HARDWARE.md) lists what was run and what should work; reports from other
machines are welcome.

## Setup rules

The server binds to `127.0.0.1` only. The launcher never runs `sudo`, sends no telemetry, asks before installing
or changing anything, and backs up any config it edits. The full list is in the
[guide](docs/GUIDE.md#setup-rules).

## Commands

| Command | Function |
|---|---|
| `ornith-launcher` or `setup` | Choose a model, download it if needed, and save a profile |
| `launch [NAME]`, `stop [NAME]` | Start a profile's server in its own window, or stop it |
| `status [NAME]`, `list` | What is running and where models are stored; the saved profiles |
| `monitor [NAME]`, `logs [NAME]`, `chat-log [NAME]` | Watch a running server, its log, or its chat log |
| `scan`, `doctor` | Show what fits this machine; check the setup (both read-only) |
| `remove NAME`, `uninstall` | Remove one profile, or choose what to remove |
| `bench [NAME]` | Long-context retrieval and perplexity tests (long, hot GPU runs) |

Every command takes `-h`; all options are in the [guide](docs/GUIDE.md#commands-and-options).

## Documentation

- [docs/GUIDE.md](docs/GUIDE.md): every screen, key, option and file, and the Linux and macOS details
- [docs/HARDWARE.md](docs/HARDWARE.md): what was tested, and supported GPUs and Linux distributions
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md): every measurement, labelled TESTED, INFERRED or ESTIMATED
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): when something goes wrong
- [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CREDITS.md](CREDITS.md),
  [CHANGELOG.md](CHANGELOG.md), [docs/LEGAL.md](docs/LEGAL.md)

## Licence

GPL-3.0-or-later (see [LICENSE](LICENSE)), with additional terms under its section 7 in [NOTICE](NOTICE): a changed
version must be marked as changed and must not present itself as ornith-launcher. Models are not part of this
project and keep their own licences.
