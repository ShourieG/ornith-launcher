# ornith-launcher

An unofficial command-line tool that sets up Ornith models on your own computer and runs them with llama.cpp. Its
aim is that trying a local LLM takes one command and a few presses of Enter, while you still see and control
every choice made for you.

Running a model locally usually means putting several parts together yourself: a model file that fits your
memory, the right llama.cpp build and flags, a context size, a way to start and stop the server, and the settings
each coding tool needs. ornith-launcher works these out from your machine and says why it recommends each choice,
and you can change any of them. Before it installs or changes anything, it asks. It keeps a record of what it
added, so uninstall removes exactly that, and it labels each memory and benchmark figure as tested, inferred or
estimated.

It is a good first step if you are new to LLMs. Setup recommends a choice at each step and says why, and
[New to local models?](#new-to-local-models) explains the words you will see. You still need the
[requirements](#requirements) in place, such as Docker on Linux, and time for the download.

It also helps if you already run models yourself. It takes over the repetitive part of each setup: checking what
fits, writing the llama.cpp command and connecting Claude Code or OpenCode. That makes it quicker to set up a model
and to compare different models, quants and contexts, since each one is saved as its own profile that you can
launch or remove on its own.

What it does for you:

- No LLM needed to set it up: setup is a plain Python script with fixed rules. It reads each model's file header,
  does the memory arithmetic and writes the llama.cpp command itself, so no chatbot or coding agent has to write a
  config or guess a flag. You need no API key or account, and the only model involved is the one you choose to run.
- Easy setup: it checks your memory and GPU (and Docker on Linux), suggests the builds that fit, and fills in the
  llama.cpp settings. Enter takes the recommended choice, and you can change any step. Nothing is installed with
  pip; the tool needs only Python.
- Extended contexts: you pick a context from 32k up to 1M tokens, with the memory each one needs shown first. Past
  the native 262k, the launcher sets up YaRN for you.
- One-click launch, if you want it: setup offers a desktop icon (default yes) and an applications menu entry
  (default no). With either one, a double-click opens a terminal and starts the server, and Ctrl+C in that window
  stops it. Without them, one command starts it.
- Coding tools, if you use them: you can connect the model to Claude Code, OpenCode, both or neither. For the
  tools you pick, setup offers the official installer when one is missing, gives Claude Code an `ornith-claude`
  command and adds the model to OpenCode's model list.

ornith-launcher is not affiliated with Ornith AI or any other company named here; see [NOTICE](NOTICE) and
[DISCLAIMER.md](DISCLAIMER.md).

> Pre-release. Linux and macOS are supported, and Windows support is planned for a later release. Linux with
> Docker has been tested on one machine (an NVIDIA DGX Spark). macOS runs without Docker and has not run on a Mac
> yet. See [What has been tested](#what-has-been-tested), [Linux](#linux) and [macOS](#macos).

## Contents

- [New to local models?](#new-to-local-models)
- [How it works](#how-it-works)
- [How it simplifies setup](#how-it-simplifies-setup)
- [One-click launcher](#one-click-launcher)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Setup, step by step](#setup-step-by-step)
- [Choosing a model yourself](#choosing-a-model-yourself)
- [Running a model](#running-a-model)
- [Using the model from other tools](#using-the-model-from-other-tools)
- [Safety and privacy](#safety-and-privacy)
- [Removing things](#removing-things)
- [What has been tested](#what-has-been-tested)
- [Commands](#commands)
- [Files and settings](#files-and-settings)
- [Linux](#linux)
- [macOS](#macos)
- [More](#more)

## New to local models?

You can run setup without knowing any of this; every choice has a recommended default. These are the words you
will see along the way:

| Word | What it means |
|---|---|
| llama.cpp | The open-source program that loads the model and answers requests. The launcher fetches a pinned build of it and starts it for you |
| GGUF | The model file format llama.cpp reads. Its header says how big the model is, which the launcher uses to work out memory |
| Quant | How finely the model's numbers are stored, such as Q8_0 (8 bits) or Q4_K_M (4 to 5 bits). Fewer bits make a smaller file that needs less memory, at some cost in quality |
| Context | How many tokens (pieces of words) the model can read at once. A longer context needs more memory |
| KV cache | The memory that holds the context while the model works. It grows with the context |
| Prefill | The time the model spends reading your prompt before it starts answering. It grows with the prompt |
| YaRN | A way to stretch a model past its native context. The launcher sets it up for contexts above 262k |
| Vision file | An extra file (`mmproj`) that lets a model read images. Setup can leave it out to save memory |
| Fits, stretch, too big | Whether a choice fits and leaves the launcher's 15% memory reserve alone, fits only by using part of that reserve, or does not fit |

## How it works

You answer setup's questions once per model:

1. Run `./ornith-launcher`. It checks your memory and GPU (and Docker on Linux), then looks for Ornith models
   already on your disk.
2. Pick a model. It suggests a few that fit this machine, each with the reason it was picked. Press Enter on
   the first suggestion and Enter again at each later step to take the recommended choice. Before anything is
   downloaded, a summary shows the model, its settings, the memory it needs and the download size and time.
3. Double-click the desktop icon, if you chose one. A terminal opens and the model server starts. Ctrl+C in that
   window stops it and frees the memory.

Without the icon, `./ornith-launcher launch` starts the model, and so does Launch a model (`l`) on setup's first
menu. On a machine without a desktop (a server, or over SSH) setup does not offer an icon, and the model runs in
the current terminal. [One-click launcher](#one-click-launcher) has the details.

On a DGX Spark the suggestions look like this (trimmed; `./ornith-launcher scan` prints the same list):

```
Memory: Unified memory (NVIDIA GB10): 121.7 GiB, about 5.9 GiB in use by the system, so plans use up to 98.4 GiB (15% kept free)

Suggested for this machine:
#  Model               Quant   Context  KV   Memory    Repo
1  Ornith 1.5 35B-A3B  Q8_0    262k     f16  46.6 GiB  ornith-ai/Ornith-1.5-35B-A3B-GGUF
2  Ornith 1.5 9B       Q8_0    262k     f16  24.3 GiB  ornith-ai/Ornith-1.5-9B-GGUF
3  Ornith 1.5 122B     Q5_K_M  262k     f16  95.7 GiB  ornith-ai/Ornith-1.5-122B-GGUF
  1. Best quality that fits, with 51.7 GiB to spare; native context; inferred from tests on DGX Spark.
  2. Best 9B, with 74.1 GiB to spare; native context; estimated, not run yet.
  3. Best 122B, with 2.7 GiB to spare; native context; estimated, not run yet.
```

The memory figures are the launcher's plans. The first is INFERRED from runs on the Spark; the 9B and 122B
figures are ESTIMATED and have not been run. Each line says which it is, and so does every row in setup.

## How it simplifies setup

### Choosing a build that fits

The launcher reads each model's GGUF header and works out the memory for the weights, the context and the KV cache,
then compares that with the memory this machine has free. It keeps 15% of memory free for everything else you run.
Every row says whether it fits: yes, stretch (it eats into that 15%, or needs more than is free right now) or too
big. You can still pick a stretch or too-big build after a warning.

### Extended contexts

Ornith models have a native context of 262k tokens. Setup offers 32k, 64k and 128k for smaller machines, the native
262k, and 524k and 1M. For the two longer ones the launcher sets the YaRN scaling flags itself (x2 for 524k, x4 for
1M).

The context screen lists every size with the memory it needs, what that leaves in the plan, what is really free
right now, and whether it fits. The longer contexts cost memory and prefill time. These runs used Ornith 1.5
35B-A3B Q8_0 on a DGX Spark:

| Measure | Native 262k | 524k (YaRN x2) | 1M (YaRN x4) |
|---|---|---|---|
| Peak memory in use | 50.9 to 51.4 GiB | 56.6 to 57.4 GiB | 67.2 to 70.0 GiB |
| Retrieval test, codes found | 2 of 2 at 240k tokens | 3 of 3 at 456k tokens | 6 of 6 at 917k tokens |
| Cold prefill | 3.5 min to 240k tokens | 9.8 min to 456k tokens | 32.5 min to 917k tokens |

Peak memory is TESTED and includes the 5 GiB or so the system used at idle. The retrieval results are TESTED for
the uncensored community build at all three sizes and for the official build at 1M, and INFERRED for the official
build at 262k and 524k. The prefill times are TESTED with the uncensored build; the official build took 35.5 min
to 917k tokens.

In the short-context perplexity test, YaRN x2 made no measurable difference and x4 cost at most 0.4% (TESTED). The
retrieval test is an easy one (exact codes hidden in a long document), so it says nothing about reasoning over long
context. These are the only long-context runs so far; for other models and sizes, the memory figures are
ESTIMATED. YaRN is documented for Ornith 1.5 but not for 1.0, and setup notes this on 1.0 rows. Full results are in
[docs/BENCHMARKS.md](docs/BENCHMARKS.md).

### Finding what you already have

The launcher looks for Ornith models in the Hugging Face cache, LM Studio, Ollama, the llama.cpp cache, `~/models`
and `~/Downloads`, and you can give any other path with `p`. It uses a model it finds where it is, without moving
or copying it.

### Downloading

Downloads come from Hugging Face without a login, and the launcher checks each file against its sha256 hash. If you
stop a download with Ctrl+C, the launcher asks whether to keep or delete what it has; a kept download shows up on
the first menu next time, and `r` resumes it with the settings you already chose.

### Starting llama.cpp with the right settings

On Linux the server runs in Docker from a llama.cpp image pinned by digest. On macOS it runs the llama.cpp release
build for the Mac, pinned by sha256, without Docker. The launcher builds the server command from your settings and
runs llama.cpp's own memory check before the first launch. You never type a llama.cpp flag, and
`./ornith-launcher status --command` shows the full command if you want to see it.

### Connecting your coding tools

Near the end, setup asks "Connect the model to a coding tool?" and offers None, OpenCode, Claude Code, or both.
Claude Code is the suggested default, since Ornith's makers recommend it; pick None if you want neither. For a
tool you pick that is not installed, setup asks "Install Claude Code now?" (or OpenCode) and runs the tool's
official installer only after a yes; say no and that tool is skipped.

If you connect Claude Code, setup:

- creates `ornith-claude-NAME` for the profile, and `ornith-claude` for the default profile, which run Claude Code
  against the local model. Plain `claude` still uses your Anthropic account.
- applies a small fix to a copy of the model's chat template, so Claude Code's mid-conversation system messages
  work. The model file is not changed.

If you connect OpenCode, setup adds the model to your OpenCode config after backing the file up. In OpenCode it
appears under `/models` as "Ornith (local, ornith-launcher)". With vision on, the entry also tells OpenCode that
the model reads images; without that, OpenCode refuses to send it any. Setup also asks whether to make the model
OpenCode's default (default yes), so new sessions start with it, in OpenCode's desktop app too; uninstall puts the
old default back. If the config has comments, which a rewrite would lose, setup shows the entry for you to paste
instead and asks before rewriting the file.

Last, setup asks whether each launch should also open the tools you connected, each in its own window and
already pointed at the model: "Open Claude Code when this model starts?", or with both connected a choice of
Neither, Claude Code, OpenCode or Both. The default is no. If you say yes, it asks which folder they open in (your
home folder by default). The tools open once the server answers, in the same terminal app as the server window,
and stay open when the server stops, since they may hold your work. Only tools that are installed and connected
are offered, so a tool you declined to install is never opened.

`./ornith-launcher setup --integrations NAME` connects a profile you set up earlier and asks the last question
again. See [Using the model from other tools](#using-the-model-from-other-tools).

### Cleaning up

Setup records everything it creates: profiles, any desktop icon or menu entry, downloaded files and any
coding-tool entries. `./ornith-launcher uninstall` lists those records and removes what you choose.

## One-click launcher

The one-click launch needs a desktop icon or an applications menu entry. Both are optional, and setup asks about
them at the end:

| Question | Default | What it creates |
|---|---|---|
| Put a launch icon for this model on your desktop? | Yes | An icon in your desktop folder |
| Also add it to your applications menu? | No | An entry in your applications menu (on macOS, `~/Applications`) |

Setup only asks the first question when your desktop folder exists. When you skip the icon, the second question
becomes "Add a launcher for this model to your applications menu?". Each one is named after the profile, so every
model you set up gets its own. If you say no to both, nothing is added to your desktop or menu, and you start the
model with `./ornith-launcher launch NAME` or with `l` on setup's first menu. To add them later, run
`./ornith-launcher setup --shortcuts NAME`. It asks the same questions and skips any icon or entry the profile
already has. What the files are on each OS is in [Linux](#linux) and [macOS](#macos).

Double-clicking the icon, or clicking the menu entry:

1. Opens the terminal you picked during setup: the system default, or another one that is installed.
2. Starts the model server with the profile's settings, fetching the llama.cpp server first if it is missing.
3. Shows the server's own log as it comes, with a status line every 15 seconds.
4. Once the server answers, opens Claude Code or OpenCode in their own windows, if you asked for that in setup.

To stop the model, press Ctrl+C in that window. It asks "Stop the server and free its memory?" and defaults to
yes. Closing the window leaves the server running, and `./ornith-launcher stop` stops it later. If another model is
already running, the launcher asks whether to stop it first (default yes), because two large models rarely fit in
memory together.

Both run `ornith-launcher launch NAME`, so the same command works from any terminal. On a machine without a
desktop, setup offers neither, and the command runs the server in the current terminal.

## Requirements

- Linux with Docker Engine, plus the NVIDIA Container Toolkit for NVIDIA GPUs; or a Mac with macOS 13.3 or newer
  (no Docker). See [Linux](#linux) and [macOS](#macos).
- Python 3.9 or newer. The tool uses only the standard library, so there is nothing to install from PyPI.
- Disk space for the model, from about 5 GiB for a 9B at Q4_K_M to hundreds of GiB for a 397B, plus the llama.cpp
  server: a few GB as a Docker image on Linux, about 12 MB on macOS.
- Memory for the model and its context, from about 10.5 GiB for a 9B at Q4_K_M with a 32k context and vision off
  (ESTIMATED).

`./ornith-launcher doctor` checks Docker or the macOS runtime, GPU access, disk space and the rest, and prints the
exact command for each problem it finds. The launcher never runs `sudo` itself. Supported GPUs and distributions
are listed in [docs/HARDWARE.md](docs/HARDWARE.md).

## Quick start

```
git clone https://github.com/ShourieG/ornith-launcher.git
cd ornith-launcher
./ornith-launcher
```

The first run shows the disclaimer and asks you to accept it. Then:

1. Press `d` to browse models (or pick a model the quick search found).
2. Press Enter on the first suggestion.
3. Press Enter at each step to take the recommended choice, and check the summary before the download starts.
4. Answer the questions at the end: Claude Code or OpenCode, the terminal for the server window, the chat log,
   the optional desktop icon and applications menu entry, and whether to open the coding tools with the model.
   Enter takes the default each time.

From then on:

| To | Do this |
|---|---|
| Start the model | Double-click its desktop icon if you chose one, or run `./ornith-launcher launch` |
| Stop it | Press Ctrl+C in its window, or run `./ornith-launcher stop` |
| See what is running | Run `./ornith-launcher status` |
| Use it from Claude Code | Run `ornith-claude` instead of `claude` |
| Add another model | Run `./ornith-launcher` again and press `d` |
| Fix a broken setup | Run `./ornith-launcher setup --repair NAME` |
| Remove things | Run `./ornith-launcher uninstall` |

Running `./ornith-launcher` with no command always opens setup. It never starts a server by itself; once a
model is set up, the first menu also offers Launch a model (`l`) and Uninstall (`u`).

## Setup, step by step

Setup runs as full-screen lists in a terminal, or as numbered menus with `--plain` (also used for pipes and SSH).
Esc goes back a step at any point before the download and keeps your choices.

| Step | What you see | What you can do |
|---|---|---|
| First menu | Ornith models already on this machine, and any stopped download | Use a found model, give a path (`p`), browse and download (`d`), resume a download (`r`), launch (`l`), uninstall (`u`) |
| Suggested | A few picks for this machine, each with the reason it was picked | Pick one, or open the best fit list (`b`), All models (`a`) or search (`/`) |
| Best fit list | Every file that can run here, each at its recommended setting, best first | Sort, filter, search, or switch to All models with `a` |
| All models | Every file in the catalog at the least memory it needs, newest and largest first | Sort, filter, search, or switch to the best fit list with `b` |
| Context size | Each context from 32k to 1M with its memory, what is left in the plan, what is really free, and a verdict | Pick a context; the recommended one is highlighted |
| Settings | Context, KV cache, parallel requests, vision and port, with the memory they need | Change any of them, then Continue |
| Models folder | Where the download goes and how much space is free | Keep the folder or type another |
| Summary | The model, settings, memory, download size and time | Go ahead, or Esc to go back |
| Finishing | The download, then the coding tools, terminal, chat log, and the optional desktop icon and menu entry | Answer each question; Enter takes the default |

The highlighted row in a list shows its file, the reason for its verdict, its badges and a link to its model card.
The badges mean:

| Badge | Meaning |
|---|---|
| TESTED | Measured on real hardware, with the results in [docs/BENCHMARKS.md](docs/BENCHMARKS.md) |
| INFERRED | Worked out from measured runs of the same architecture |
| ESTIMATED | Calculated from the GGUF header and the memory model only; never run |
| UNTESTED LOCALLY | A size the project has never run on any machine (the 397B) |
| LOW-BIT | A 1- or 2-bit quant (IQ1, IQ2, Q1, Q2, TQ1, TQ2); expect lower quality |
| VERSION UNKNOWN | The Ornith version could not be told from the repo, the file or its lineage |
| UNCENSORED | Labelled as a model with its safety behaviour removed (see below) |
| NO LICENCE, OTHER LICENCE | The repo declares no licence, or a custom one; read the model card |

Setup saves a profile for every model you set up. The first profile becomes the default, so commands without a
name use it.

## Choosing a model yourself

Besides the suggestions, setup has two full lists:

- Best fit list (`b`): only the files that can run on this machine, each at its recommended setting, best
  first.
- All models (`a`): every Ornith file the catalog knows, official and community uploads alike, each at the
  least memory it can run with (32k context, vision off). Fit never hides or moves a row here, so you can see what
  a bigger machine would get.

Picking a row from All models starts the next screens from that file's recommended setting, and asks first if it
is a stretch or too big. The catalog ships with the tool so setup works offline; `./ornith-launcher scan
--refresh` updates it from Hugging Face.

### Keys

| Key | Where | What it does |
|---|---|---|
| ↑ ↓, Page Up, Page Down | Lists | Move the highlight |
| Enter | Everywhere | Pick the highlighted row, or accept an answer |
| Esc | Everywhere | Back one step, keeping your choices |
| `m` | Setup menus | Back to the first menu |
| `q` | Menus | Quit; the screen is cleared and you are told what was kept |
| `n` `p`, or → ← | Long lists | Next or previous page |
| `b` `a` | Model lists | The best fit list or All models |
| `s` | Model lists | Sort |
| `f` | Model lists | Filters |
| `/` | Model lists | Search the repo and file names |
| `c` | Model lists, filters | Clear the search and filters, or clear one filter |
| Ctrl+click | Model lists | Open the model card (Cmd+click on macOS) |

In plain menus you type the number or letter and press Enter; Esc then Enter (or typing `esc`) goes back.

### Sorts

The best fit list sorts by best first or best for small memory. Best for small memory is a combined score of
memory left, quant, size, context, popularity and trust, and setup labels it a heuristic.

Both lists also sort by memory, download size, quant, likes, downloads in the last 30 days, lifetime downloads and
last update. The quant sort goes by bits per weight, so NVFP4, MXFP4, IQ and UD quants sit on one scale. All
models adds model order and model size.

On a small machine, sort All models by "memory needed, smallest first", or the best fit list by "best for small
memory".

### Filters

Filters apply to both lists, and each one shows how many files match and how many of those fit:

| Filter | Options |
|---|---|
| Model size | 9B, 35B, 122B, 397B (tick any) |
| Version | 1.5, 1.0 (tick any) |
| Quant | 16 bits, 8 bits, 5 to 6 bits, 4 bits, below 4 bits (tick any) |
| Source | Any, official (Ornith AI) only, community only |
| Licence | Any, declares a licence |
| Vision | Any, can read images |
| Updated | Any time, or in the last 7, 30, 90 or 365 days |
| Likes | Any, or 10, 100 or 1,000 or more |
| Downloads (30 days) | Any, or 100 to 100,000 or more |
| Downloads (lifetime) | Any, or 1,000 to 1,000,000 or more |
| Uncensored | Hidden, shown, or only uncensored models |

Search (`/`) narrows the list without changing its order. Sorts, filters and search last until setup ends.

### Uncensored models

Some community models have had their safety behaviour removed. They stay hidden until you turn them on, which
shows a warning first, and choosing one means typing "I understand". The label is a best-effort guess from names,
tags and the models a file was made from, so it can miss some; read the model card before using a community
model. See [DISCLAIMER.md](DISCLAIMER.md).

### Settings

After the context screen, a Settings menu lets you change:

| Setting | What it changes |
|---|---|
| Context | How many tokens the model can see at once, from 32k to 1M. Past the model's native length the launcher uses YaRN, which slows the prefill |
| KV cache | f16 by default; q8_0 needs about half the KV cache memory but is UNTESTED with Ornith |
| Parallel requests | How many requests the server handles at once (4 by default). They share one context pool, so more of them need only a little more memory |
| Vision | Whether the image projector is loaded, for models that have one |
| Port | The port on 127.0.0.1 the server listens on (18400 by default, or the next free one) |

Each option shows the memory it needs and whether it fits, and a change applies as soon as you pick it.

## Running a model

The [one-click launcher](#one-click-launcher) covers starting and stopping. From a terminal:

- `./ornith-launcher status` shows what is running, how much memory it uses and where the models are stored.
- `./ornith-launcher monitor` watches a server that is already running.
- `./ornith-launcher logs` follows its log.

### Chat log

Setup can give a model a chat log. Every launch then opens a second window that shows each request sent to the
model and its reply as it streams: thinking, answer, tool calls, tokens and speed. It is useful for seeing what a
coding tool sends to the model.

A small proxy on the model's port passes everything through, so clients keep the same URL. It keeps the last 10
requests in memory and writes nothing to disk. Closing the chat log window closes only that window; press `c` and
Enter in the server window to open it again. A desktop icon or menu entry also gets a "Show chat log" action, and
`launch --chat-log` or `--no-chat-log` changes the setting for one launch.

## Using the model from other tools

The server listens on `127.0.0.1` at the profile's port and speaks the OpenAI-compatible API that llama.cpp
provides, so most tools that take a custom OpenAI base URL can use it. With the default port:

```
curl http://127.0.0.1:18400/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello"}]}'
```

`./ornith-launcher status` shows the port each profile uses.

### Claude Code

Run `ornith-claude` (the default profile) or `ornith-claude-NAME` instead of `claude`. Your own Claude Code settings
are not changed. Setup creates these commands when you connect Claude Code; see
[Connecting your coding tools](#connecting-your-coding-tools).

Anthropic does not support Claude Code with non-Claude models; see [DISCLAIMER.md](DISCLAIMER.md).

In auto mode, Claude Code checks each action it would otherwise ask you about by sending a separate request to the
model, so through `ornith-claude` the local Ornith model makes those checks. Each check carries more than 140,000
characters of Claude Code's own instructions (TESTED with Claude Code 2.1.312), so it adds a prefill wait before
the action runs. Claude Code's checks are written for Claude models, and how well an Ornith model judges them has
not been tested. Manual mode (Shift+Tab) asks you instead. The wrapper sets `CLAUDE_CODE_AUTO_MODE_SERVER=0`,
because llama.cpp cannot run Anthropic's server-side checks; without it, Claude Code stops each auto mode session
at a notice about classifier charges.

### OpenCode

Pick "Ornith (local, ornith-launcher)" under `/models`, or nothing at all if you made it the default. Uninstall
removes only the entry setup added, and never entries you edited yourself. It puts back the default model you had
before setup, unless you picked another one since.

Ornith models think before they answer, and OpenCode shows the thinking. They have no effort levels, so instead of
low, medium and high the entry offers one variant, "no-thinking", for quick answers: pick it with OpenCode's
variant switch or `opencode run --variant no-thinking`. Without a variant the model thinks.

OpenCode's chat (the desktop app and `opencode web`) shows a picture when it is part of the model's reply, written
as an image with a `data:` address, but not an image a tool read and not a file on disk (checked in OpenCode 1.19).
So setup also adds a short file, `show-pictures.md` in the launcher's data folder, to OpenCode's `instructions`.
OpenCode puts the files listed there into the system prompt of every session, for every model you use in
OpenCode, and setup says so before you pick OpenCode and again when it adds the file. The file tells the model to
draw charts and diagrams as SVG inside its reply. A model that can run commands writes the SVG to a file, and a
short script checks it, encodes only `%` and `#` and prints the whole tag for the model to paste; otherwise it
writes the tag by hand from a complete example and strict rules. Uninstall lists the file and takes it out again
with the last Ornith model. With vision on, the model also sees the images you attach and the images its tools
read.

## Safety and privacy

- The server binds to `127.0.0.1` only, so other machines on your network cannot reach it.
- Downloads are pinned and checked: the llama.cpp image by digest, the macOS llama.cpp build and model files by
  sha256.
- The launcher never runs `sudo`. When something needs root, such as adding you to the `docker` group, it prints
  the command for you to run.
- It asks before installing software, deleting files or changing another tool's config, and backs up a config
  file before changing it.
- It never moves, copies or deletes models that belong to other apps.
- It sends no telemetry. It connects only to Hugging Face, ghcr.io for the llama.cpp image (Linux), the llama.cpp
  releases on github.com (macOS) and, if you choose them, the official Claude Code and OpenCode installers.
- Ctrl+C stops every long-running command cleanly, including the containers and processes it started.

## Removing things

`./ornith-launcher uninstall` offers:

- "Remove one setup". You then choose what goes:

  | Choice | Removes |
  |---|---|
  | Setup only (the default) | The profile, its launch icon and menu entry, its OpenCode entry and its Claude Code wrapper and alias, where they exist. The model stays. |
  | Setup and its model | The same, plus the model it downloaded. A model found elsewhere is never deleted, so this is not offered for one. |
  | The model server | The llama.cpp image on Linux, or the llama.cpp runtime folder on macOS. The setup stays. |
  | Setup, model and model server | All three. |

- "Remove all setups" asks whether to keep the models they downloaded (the default) or delete them too.
- "Remove unfinished downloads" deletes the downloads you stopped and keeps finished models.
- "Remove everything" deletes all setups, every model it downloaded, the model server it fetched and its own
  settings.

It removes only what the launcher's own records list. Models it found elsewhere on the machine are never touched,
and the model server is not offered if it was there before setup or another setup still uses it.
`./ornith-launcher remove NAME` removes a single profile and asks before deleting downloaded files.

## What has been tested

- Machine: NVIDIA DGX Spark (GB10, 128 GB unified memory, arm64) with Ubuntu 24.04.
- Model: Ornith 1.5 35B-A3B. Its results are in [docs/BENCHMARKS.md](docs/BENCHMARKS.md), each labelled
  TESTED, INFERRED or ESTIMATED.
- On that machine: setup with a real download and resume, launching from the desktop icon, the chat log, Claude
  Code and OpenCode through it, `setup --shortcuts`, every uninstall choice, and Ctrl+C in `bench`.
- Untested: other Linux distributions, discrete NVIDIA, AMD and Intel GPUs, CPU-only machines, and every other
  model and size. Plans for them are ESTIMATED from the GGUF headers and the memory model, and the 397B is also
  marked UNTESTED LOCALLY.
- macOS: not run on a Mac yet. Unit tests simulate a Mac's commands and run on Linux; every macOS figure is
  ESTIMATED.

Reports from other machines are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Commands

| Command | What it does |
|---|---|
| `ornith-launcher` | Open setup (the same as `setup`) |
| `ornith-launcher setup` | Choose a model, download it if needed, and save a profile |
| `ornith-launcher launch [NAME]` | Start a profile's server and watch it |
| `ornith-launcher stop [NAME]` | Stop a server and free its memory |
| `ornith-launcher status [NAME]` | What is running, and where models are stored |
| `ornith-launcher list` | List the profiles |
| `ornith-launcher monitor [NAME]` | Watch a server that is already running |
| `ornith-launcher logs [NAME]` | Follow a server's own log; Ctrl+C closes only the log |
| `ornith-launcher chat-log [NAME]` | Follow the chat log of a model that has one |
| `ornith-launcher scan` | Show what fits this machine (read-only) |
| `ornith-launcher doctor` | Check Docker or the macOS runtime, the GPU, disk, profiles and tools (read-only) |
| `ornith-launcher remove NAME` | Remove a profile; asks before deleting downloaded files |
| `ornith-launcher uninstall` | Remove one setup, all setups, unfinished downloads, or everything the launcher created |
| `ornith-launcher bench [NAME]` | Long-context retrieval and perplexity tests (long, hot GPU runs) |

`NAME` is a profile name; without it, commands use the default profile. Every command takes `-h` for its help.
Run from a clone, the command is `./ornith-launcher`.

### Options

Global options go before the command.

| Option | What it does |
|---|---|
| `--version` | Print the version |
| `--verbose` | Log details to stderr |

`setup`:

| Option | What it does |
|---|---|
| `--model PATH` | Use this model file or folder and skip the search |
| `--models-dir PATH` | Folder for downloaded models (default `~/models/ornith-launcher`) |
| `--port PORT` | Port for the server (default 18400, or the next free one) |
| `--name NAME` | Profile name (default: made from the model and context) |
| `--plain` | Numbered menus instead of full-screen lists |
| `--yes` | Accept every default without asking |
| `--accept-uncensored` | Accept an uncensored model with `--yes`; `--yes` alone never does |
| `--repair NAME` | Put back what a profile needs: the model server, a missing download, a changed model file |
| `--integrations NAME` | Connect an existing profile to Claude Code or OpenCode |
| `--shortcuts NAME` | Add a desktop icon or applications menu entry for an existing profile |

`launch`:

| Option | What it does |
|---|---|
| `--here` | Run in this terminal instead of opening a window |
| `--no-monitor` | Start the server and return |
| `--chat-log` | Open the chat log window this time |
| `--no-chat-log` | No chat log this time |
| `--no-tools` | Do not open the coding tools this time |

`scan` (`--version`, `--size`, `--source` and `--quant` can be given more than once):

| Option | What it does |
|---|---|
| `--refresh` | Rescan Hugging Face first (anonymous, metadata only) |
| `--full` | List every combination with its memory and fit, not only the suggestions |
| `--version V` | Only this Ornith version, for example `1.5` |
| `--size S` | Only this size, for example `35B` |
| `--source official\|community` | Only official or community builds |
| `--quant Q` | Only this quant, for example `Q8_0` |
| `--fits` | Hide what is too big or eats into the 15% of memory kept free |
| `--search WORDS` | Words that must appear in the repo or file name |
| `--uncensored` | Also list uncensored models |
| `--untested-kv` | Also list the q8_0 KV cache |
| `--no-low-bit` | Hide 1- and 2-bit quants |
| `--limit N` | Rows in `--full` (default 40) |

Other commands:

| Command | Option | What it does |
|---|---|---|
| `status` | `--command` | Also show the command that starts the server |
| `chat-log` | `--window` | Open it in a new window, closing one that is still open |
| `remove` | `--yes` | Accept the defaults |
| `bench` | `--needle` | Long-context retrieval test |
| `bench` | `--perplexity` | Short-context quality test |
| `bench` | `--lengths N,N,...` | Prompt lengths in tokens for `--needle` (default 120000,250000,480000,950000) |

`bench` runs the GPU hot for a long time and pauses when it gets too hot. Results so far are in
[docs/BENCHMARKS.md](docs/BENCHMARKS.md).

## Files and settings

| What | Where |
|---|---|
| Settings and profiles | `~/.config/ornith-launcher` (or `$XDG_CONFIG_HOME/ornith-launcher`) |
| Model catalog cache | `~/.cache/ornith-launcher` |
| Downloaded models | `~/models/ornith-launcher`, or the folder you pick |
| Claude Code wrappers | `~/.local/bin/ornith-claude` and `~/.local/bin/ornith-claude-<profile>` |
| Desktop icon and menu entry | See [Linux](#linux) and [macOS](#macos) |

| Environment variable | What it does |
|---|---|
| `HF_TOKEN` | Sent to huggingface.co for downloads, if you set it; the launcher never asks for one |
| `HF_ENDPOINT` | Use a Hugging Face mirror |
| `HF_HOME`, `HF_HUB_CACHE` | Where the quick search looks for the Hugging Face cache |
| `LLAMA_CACHE`, `OLLAMA_MODELS` | Where it looks for llama.cpp and Ollama models |
| `XDG_CONFIG_HOME`, `XDG_DATA_HOME` | Where settings and menu entries go |

## Linux

Linux runs llama.cpp in Docker. This is the tested setup (a DGX Spark with Ubuntu 24.04); other distributions and
GPUs are listed in [docs/HARDWARE.md](docs/HARDWARE.md).

### Requirements on Linux

- Docker Engine. Docker Desktop runs containers in a VM with its own memory limit, and `doctor` warns when that
  limit is below the machine's memory.
- For an NVIDIA GPU, the driver and the NVIDIA Container Toolkit. `doctor` prints the commands to install and
  configure it.

### The server on Linux

Each profile runs in its own container, `ornith-launcher-NAME`, from a llama.cpp image pinned by digest and
published on `127.0.0.1` only. `docker logs ornith-launcher-NAME` shows the same log as `./ornith-launcher logs`.

### One-click launch on Linux

The icon and the menu entry are `.desktop` files named "Ornith: NAME": one in your desktop folder (from
`~/.config/user-dirs.dirs`, else `~/Desktop`) and one in `~/.local/share/applications`. On GNOME the launcher marks
its own desktop icon as trusted, so it starts on the first double-click. Other desktops may ask you to allow
launching once. If you turned on the chat log, right-clicking the icon or menu entry also offers "Show chat log",
on desktops that show actions.

The terminals it can open are the system default and GNOME Terminal, Konsole, Ptyxis, GNOME Console, kitty,
Alacritty, WezTerm, foot, Xfce Terminal, Tilix, Terminator, MATE Terminal, LXTerminal and xterm, when installed.

## macOS

macOS runs the llama.cpp release build for the Mac as a normal process, without Docker. This support is new: it
has not run on a Mac yet, and the behaviour below is what the code does, checked by unit tests that simulate a
Mac. Reports from Mac users are welcome.

### Requirements on macOS

- macOS 13.3 or newer, which the pinned llama.cpp build needs. Setup stops with that message on older versions.
- An Apple Silicon Mac, where the model runs on the GPU through Metal. An Intel Mac runs it on the CPU only, which
  is much slower, so only small models are practical there.
- Python 3.9 or newer: `xcode-select --install` provides 3.9, or install a newer one with `brew install python`.
  The launcher finds `python3.12` and the like next to an older `python3`.

### The server on macOS

The first setup downloads llama.cpp b11552 for macOS (about 12 MB) from the llama.cpp releases on GitHub, checks
it against its pinned sha256 and unpacks the whole folder into `~/.local/share/ornith-launcher/runtime`. Each
profile's server then runs from there on `127.0.0.1` only, in its own session, so Ctrl+C in the server window asks
before stopping it and closing the window leaves it running. Its log is in
`~/.local/state/ornith-launcher/logs/NAME.log`. While it runs, `caffeinate` keeps the Mac from sleeping when idle,
so a long prompt is not cut off; closing the lid still puts it to sleep.

The GPU on a Mac can use only part of the memory, a limit Metal sets. Setup plans against that limit. Before the
runtime is installed it uses an estimate (two thirds of the memory up to 36 GiB, three quarters above, ESTIMATED);
after that it reads the limit from llama.cpp. The launcher never changes the limit.

Files the launcher downloads itself are not marked as coming from the internet, so Gatekeeper should not block
them. If `./ornith-launcher doctor` finds that mark on the runtime, it prints the `xattr` command that clears it.

macOS reports no GPU temperature without root, so the server window shows memory and swap but no temperature,
and `bench` cannot pause for heat.

### One-click launch on macOS

The icon and the menu entry are `.command` files named "Ornith NAME.command": one on your Desktop and one in
`~/Applications`, where Finder and Spotlight find it. Finder opens them in Terminal. If macOS asks before opening
one the first time, choose Open. A `.command` file has no right-click actions, so reopen the chat log with c and
Enter in the server window, or with `./ornith-launcher chat-log NAME`.

The terminals it can open are Terminal (the default), iTerm2 when installed, and kitty, Alacritty and WezTerm
when they are on your PATH. Terminal and iTerm2 are opened with `open -a` and a small script that deletes itself,
so macOS does not ask for Automation permission.

### Other differences on macOS

- The quick search for models already on the Mac skips `~/Downloads`, because macOS asks before an app reads
  that folder. Give a path in it with `p`, and macOS asks once.
- Claude Code and OpenCode use the same official installers and config files as on Linux. If `~/.local/bin` is
  not on your PATH, setup prints the line to add to `~/.zprofile`.

| What | Where on macOS |
|---|---|
| llama.cpp runtime | `~/.local/share/ornith-launcher/runtime` |
| Server logs | `~/.local/state/ornith-launcher/logs` |
| Desktop icon and menu entry | `~/Desktop` and `~/Applications` |

To check it on a Mac, with any Ornith GGUF that fits:

```
ORNITH_IT=1 ORNITH_IT_MODEL=/path/to/model.gguf python3 -m unittest tests.integration.test_native -v
```

It installs the runtime into a temporary folder, starts and stops a server, and prints the Metal limit and
llama.cpp's own memory estimate.

## More

- Supported hardware and Linux distributions: [docs/HARDWARE.md](docs/HARDWARE.md)
- Benchmarks: [docs/BENCHMARKS.md](docs/BENCHMARKS.md)
- When something goes wrong: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- Licences and trademarks: [docs/LEGAL.md](docs/LEGAL.md)
- Credits: [CREDITS.md](CREDITS.md)
- Changes: [CHANGELOG.md](CHANGELOG.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: see [SECURITY.md](SECURITY.md).

## Licence

Apache-2.0, see [LICENSE](LICENSE). Models are not part of this project and keep their own licences.
