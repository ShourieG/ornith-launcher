# ornith-launcher

An unofficial tool that runs Ornith models on your own computer with one command. ornith-launcher picks a build
that fits your memory, downloads and checks it, writes the llama.cpp settings, and can connect Claude Code or
OpenCode to the model, showing you every choice and why it made it.

It is not affiliated with Ornith AI or any other company named here; see [NOTICE](NOTICE) and
[DISCLAIMER.md](DISCLAIMER.md).

> Pre-release. Linux (with Docker) and macOS (without Docker) are supported; Windows is planned for a later
> release. Linux has been tested on one machine, an NVIDIA DGX Spark, and macOS on one Mac, a MacBook Pro with an
> Apple M1 Max. See [Platforms tested](#platforms-tested).

![Setup's best fit list on a MacBook Pro with 32 GB: Ornith files that fit, each with its context, memory, download
size and popularity, and the highlighted file's details and Mac note below](docs/images/best-fit-list-macos.png)

Setup's best fit list on a MacBook Pro with 32 GB: the 913 Ornith files on Hugging Face that fit this Mac, best
first, each at the context and memory setup would use (532 more are too big, and All models shows them). The
highlighted row shows its file, a Mac-specific note and its model card. Every memory figure says whether it was
measured (TESTED), worked out from measured runs (INFERRED) or only calculated (ESTIMATED).

## Quick start

```
git clone https://github.com/ShourieG/ornith-launcher.git
cd ornith-launcher
./ornith-launcher
```

You need Python 3.9 or newer, and Docker on Linux; see [Requirements](#requirements). The first run shows the
disclaimer and asks you to accept it. Then:

1. Press `d` to browse models, or pick one the quick search found.
2. Press Enter on the first suggestion, and Enter at each step to take the recommended choice. A summary shows
   the model, settings, memory and download size before anything is fetched.
3. Answer the questions at the end: Claude Code or OpenCode, the terminal for the server window, the chat log,
   the launch icon, and whether to open the coding tools with the model. Enter takes the default each time.

From then on:

| To | Do this |
|---|---|
| Start the model | Double-click its launch icon, or run `./ornith-launcher launch` |
| Stop it | Press Ctrl+C in its window, or run `./ornith-launcher stop` |
| See what is running | `./ornith-launcher status` |
| Use it from Claude Code | `ornith-claude` instead of `claude` |
| Use it from OpenCode | `opencode`, then pick "Ornith (local, ornith-launcher)" under `/models` if it is not the default |
| Add another model | Run `./ornith-launcher` again and press `d` |
| Fix a broken setup | `./ornith-launcher setup --repair NAME` |
| Remove things | `./ornith-launcher uninstall` |

Not sure whether a bigger model at a low quant beats a smaller one at a high quant, or how much context to keep?
[Choosing a size, a quant and a context](docs/GUIDE.md#choosing-a-size-a-quant-and-a-context) sums up what
published studies suggest, with links to them.

Running `./ornith-launcher` with no command always opens setup; it never starts a server by itself. Every screen
and option is described in [docs/GUIDE.md](docs/GUIDE.md).

## Motivation

Running a model locally takes several parts, each with its own documentation:

- a model file that fits your memory
- a llama.cpp build and the right flags for it
- a context size
- a way to start and stop the server
- settings for each coding tool you use

A wrong guess often shows up only after a long download, as a model that does not load or runs out of memory.

ornith-launcher works these out with fixed rules. It reads each model's GGUF header, calculates the memory for this
machine and writes the llama.cpp command itself; no chatbot or coding agent writes a config for you. Every step has
a recommended choice that Enter accepts, and every choice can be changed. It asks before installing or changing
anything, records what it adds so uninstall can remove exactly that, and labels every figure TESTED, INFERRED or
ESTIMATED.

It suits newcomers and people who already run models. If you are new, pressing Enter through setup gives you a
model that runs, and the [guide](docs/GUIDE.md#new-to-local-models) explains the terms. If you already run models,
each model, quant and context you try is saved as a profile you can launch, compare or remove on its own.

## Environment setup simplification

- Suggests builds that fit. It checks your memory and GPU (Docker on Linux, Metal's GPU memory limit on macOS)
  and marks every file as fits, stretch or too big, keeping 15% of memory free for everything else you run.
- Offers contexts from 32k to 1M tokens, with the memory each one needs shown first. Past the native 262k it sets
  up YaRN for you.
- Finds Ornith models you already have (Hugging Face cache, LM Studio, Ollama, the llama.cpp cache, `~/models`)
  and uses them where they are, without moving or copying them.
- Downloads from Hugging Face without a login, pinned to a revision and checked by sha256. Ctrl+C keeps a stopped
  download, and setup resumes it later with the same settings.
- Fetches a pinned llama.cpp b11552: a Docker image pinned by digest on Linux, the official release build pinned by
  sha256 on macOS. The tool itself needs only Python, with nothing to install from PyPI.
- Makes a launch icon if you want one: a double-click opens a terminal, starts the server and, if you like, opens
  Claude Code or OpenCode next to it. Ctrl+C in that window stops the model and frees its memory.
- Connects Claude Code (an `ornith-claude` command, with a chat-template fix it needs) and OpenCode (a provider
  entry, made the default if you agree), and installs either one with its official installer after you say yes.
- Shows what a coding tool sends to the model, if you turn on the chat log: every request and its reply as it
  streams, kept in memory only.

## Platforms tested

| Platform | Machine | What was run |
|---|---|---|
| Linux | NVIDIA DGX Spark (GB10, 128 GB unified memory, arm64), Ubuntu 24.04, Ornith 1.5 35B-A3B | Setup with a real download and resume, launching from the desktop icon, the chat log, Claude Code and OpenCode through it, `setup --shortcuts`, every uninstall choice, and Ctrl+C in `bench` |
| macOS | MacBook Pro, Apple M1 Max, 32 GB, macOS 26.6.2, community Ornith 1.5 9B quants | Setup with real downloads, the llama.cpp runtime download, the launch icon app opening iTerm2 windows, installing OpenCode, uninstall, and the Metal memory limit the launcher reads |

Not run yet: other Linux distributions, discrete NVIDIA, AMD and Intel GPUs, CPU-only machines, Intel Macs,
`bench` on a Mac, and every other model and size. Plans for those are ESTIMATED from the GGUF headers and the
memory model, and the 397B is also marked UNTESTED LOCALLY. The unit tests run on Linux and macOS in CI.
[docs/HARDWARE.md](docs/HARDWARE.md) lists what should work, and reports from other machines are welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Requirements

- Linux with Docker Engine, plus the NVIDIA Container Toolkit for NVIDIA GPUs; or a Mac with macOS 13.3 or newer.
- Python 3.9 or newer. On a Mac, `xcode-select --install` provides one.
- Disk space for the model, from about 5 GiB for a 9B at Q4_K_M to hundreds of GiB for a 397B, plus the llama.cpp
  server: a few GB as a Docker image on Linux, about 12 MB on macOS.
- Memory for the model and its context, from about 10.5 GiB for a 9B at Q4_K_M with a 32k context (ESTIMATED).

`./ornith-launcher doctor` checks all of this and prints the exact command for each problem it finds. The launcher
never runs `sudo` itself.

## Contexts up to 1M tokens

Ornith models have a native context of 262k tokens. Setup also offers 32k to 128k for smaller machines, and 524k
and 1M with YaRN (x2 and x4). Longer contexts cost memory and prefill time. These runs used Ornith 1.5 35B-A3B Q8_0
on a DGX Spark:

| Measure | Native 262k | 524k (YaRN x2) | 1M (YaRN x4) |
|---|---|---|---|
| Peak memory in use | 50.9 to 51.4 GiB | 56.6 to 57.4 GiB | 67.2 to 70.0 GiB |
| Retrieval test, codes found | 2 of 2 at 240k tokens | 3 of 3 at 456k tokens | 6 of 6 at 917k tokens |
| Cold prefill | 3.5 min to 240k tokens | 9.8 min to 456k tokens | 32.5 min to 917k tokens |

Peak memory is TESTED and includes the 5 GiB or so the system used at idle. The retrieval results are TESTED for
the uncensored community build at all three sizes and for the official build at 1M, and INFERRED for the official
build at 262k and 524k. The prefill times are TESTED with the uncensored build; the official build took 35.5 min
to 917k tokens. In the short-context perplexity test, YaRN x2 made no measurable difference and x4 cost at most
0.4% (TESTED).

The retrieval test is an easy one (exact codes hidden in a long document), so it says nothing about reasoning over
long context. These are the only long-context runs so far; for other models and sizes the memory figures are
ESTIMATED. YaRN is documented for Ornith 1.5 but not for 1.0, and setup notes this on 1.0 rows. Full results are in
[docs/BENCHMARKS.md](docs/BENCHMARKS.md).

## One-click launch support

At the end of setup you can add a launch icon on your desktop (default yes) and an entry in your applications
menu (default no). On Linux they are `.desktop` files; on macOS they are small "Ornith NAME" apps that setup builds
with macOS's own `osacompile`, which also show in Launchpad and Spotlight.

A double-click opens the terminal you picked in setup, starts the server with the profile's settings, and shows
its log with a status line every 15 seconds, with the chat log in a second window if you turned it on. Once the
server answers, it opens Claude Code or OpenCode in their own windows if you asked for that. Ctrl+C in the server
window asks before stopping the model and freeing its memory. Closing the window leaves the server running, and
`./ornith-launcher stop` stops it later.

Without an icon, `./ornith-launcher launch NAME` does the same from any terminal. On a machine without a desktop,
such as a server or an SSH session, the model runs in the current terminal. `./ornith-launcher setup --shortcuts
NAME` adds an icon later.

## Claude Code and OpenCode

Near the end, setup asks which coding tool to connect: Claude Code (the suggested default, since Ornith's makers
recommend it), OpenCode, both or none.

- Claude Code gets `ornith-claude` and `ornith-claude-NAME`, which run Claude Code against the local model. Plain
  `claude` still uses your Anthropic account, and your Claude Code settings are not changed. Setup fixes a copy of
  the model's chat template so Claude Code's mid-conversation system messages work. Anthropic does not support
  Claude Code with non-Claude models; see [DISCLAIMER.md](DISCLAIMER.md).
- OpenCode gets a provider entry, after setup backs up its config: "Ornith (local, ornith-launcher)" under
  `/models`, with image input when vision is on, and as OpenCode's default model if you agree.

If a tool is missing, setup offers its official installer and runs it only after a yes, with a spinner while the
installer prints nothing. Afterwards it checks that a new terminal window can find the `claude` or `opencode`
command, and prints the PATH line to add if not. Setup records each tool it installs, so uninstall can remove it
later. `./ornith-launcher setup --integrations NAME` connects a profile you set up earlier.

The model also works with anything that speaks the OpenAI-compatible API on `127.0.0.1` at the profile's port
(18400 by default). The [guide](docs/GUIDE.md#using-the-model-from-other-tools) has the details, including Claude
Code's auto mode and OpenCode's picture instructions.

## Uninstalling

`./ornith-launcher uninstall` removes only what the launcher's own records say it created, and lists everything it
will delete before asking:

- Remove one setup: the setup alone (the default), with its model, the model server, or all three.
- Remove all setups: the setups alone (the default), with their models, with their models and llama.cpp, or
  everything the launcher created.
- Remove unfinished downloads, and remove the coding tools setup installed (their own settings and history stay
  unless you ask).

Models it found elsewhere on your machine are never touched, and the model server is kept if it was there before
setup or another setup still uses it. Each choice is described in the
[guide](docs/GUIDE.md#uninstalling).

## Setup rules

- The server binds to `127.0.0.1` only, so other machines on your network cannot reach it.
- Downloads are pinned and checked: the llama.cpp image by digest, the macOS llama.cpp build and model files by
  sha256.
- The launcher never runs `sudo`. When something needs root, such as adding you to the `docker` group, it prints
  the command for you to run.
- It asks before installing software, deleting files or changing another tool's config, and backs up a config
  file before changing it. It prints PATH lines for you to add rather than editing your shell files.
- It never moves, copies or deletes models that belong to other apps.
- It sends no telemetry. It connects only to Hugging Face, ghcr.io for the llama.cpp image (Linux), the llama.cpp
  releases on github.com (macOS) and, if you choose them, the official Claude Code and OpenCode installers.
- Ctrl+C stops every long-running command cleanly, including the containers and processes it started.

## Commands

| Command | Function |
|---|---|
| `ornith-launcher` or `setup` | Choose a model, download it if needed, and save a profile |
| `launch [NAME]`, `stop [NAME]` | Start a profile's server in its own window, or stop it |
| `status [NAME]`, `list` | What is running and where models are stored; the saved profiles |
| `monitor [NAME]`, `logs [NAME]`, `chat-log [NAME]` | Watch a running server, its log, or its chat log |
| `scan` | Show what fits this machine (read-only) |
| `doctor` | Check Docker or the macOS runtime, the GPU, disk, profiles and tools (read-only) |
| `remove NAME`, `uninstall` | Remove one profile, or choose what to remove |
| `bench [NAME]` | Long-context retrieval and perplexity tests (long, hot GPU runs) |

Without `NAME`, commands use the default profile, the first one you set up. Every command takes `-h`, and every
option is listed in the [guide](docs/GUIDE.md#commands-and-options).

## Documentation

- [docs/GUIDE.md](docs/GUIDE.md): every screen, key, option and file, and the Linux and macOS details
- [docs/HARDWARE.md](docs/HARDWARE.md): supported GPUs and Linux distributions
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md): every measurement, labelled TESTED, INFERRED or ESTIMATED
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): when something goes wrong
- [docs/LEGAL.md](docs/LEGAL.md), [CREDITS.md](CREDITS.md), [CHANGELOG.md](CHANGELOG.md)

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md), and [SECURITY.md](SECURITY.md) for security
issues.

## Licence

GPL-3.0-or-later, see [LICENSE](LICENSE), with additional terms under its section 7 in [NOTICE](NOTICE). You may
share and change ornith-launcher, but a copy you distribute, changed or not, must keep the notices and come with its
source under the same licence. A changed version must also be marked as changed and must not present itself as
ornith-launcher or as made or endorsed by its authors. Models are not part of this project and keep their own
licences.
