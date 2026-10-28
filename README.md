# ornith-launcher

Set up an Ornith model on your own machine with one command. Setup works out what fits and recommends a choice at
each step; you decide what to keep and what it adds to your machine.

- Easy setup: it checks your memory, GPU and Docker, suggests the builds that fit, and fills in the llama.cpp
  settings. Enter takes the recommended choice, and you can change any step.
- Extended contexts: you pick a context from 32k up to 1M tokens, with the memory each one needs shown first. Past
  the native 262k, the launcher sets up YaRN for you.
- One-click launch, if you want it: setup offers a desktop icon (default yes) and an applications menu entry
  (default no). With either one, a double-click opens a terminal and starts the server, and Ctrl+C in that window
  stops it. Without them, one command starts it.
- Coding tools, if you use them: you can connect the model to Claude Code, OpenCode, both or neither. For the
  tools you pick, setup offers the official installer when one is missing, gives Claude Code an `ornith-claude`
  command and adds the model to OpenCode's model list.

ornith-launcher is an unofficial command-line tool. It is not affiliated with Ornith AI or any other company
named here; see [NOTICE](NOTICE) and [DISCLAIMER.md](DISCLAIMER.md).

> Early development. Linux with Docker only for now, tested on one machine (a DGX Spark); Windows and macOS are
> planned. See [What has been tested](#what-has-been-tested).

## Contents

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
- [More](#more)

## How it works

You answer setup's questions once per model:

1. Run `./ornith-launcher`. It checks your memory, GPU and Docker, then looks for Ornith models already on
   your disk.
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

The server runs in Docker from a llama.cpp image pinned by digest. The launcher builds the server command from your
settings and runs llama.cpp's own memory check before the first launch. You never type a llama.cpp flag, and
`./ornith-launcher status --command` shows the full command if you want to see it.

### Connecting your coding tools

Near the end, setup asks "Connect the model to a coding tool?" and offers None, OpenCode, Claude Code, or both. Both
is the default, so pick None if you want neither. For a tool you pick that is not installed, setup asks "Install
Claude Code now?" (or OpenCode) and runs the tool's official installer only after a yes; say no and that tool is
skipped.

If you connect Claude Code, setup:

- creates `ornith-claude-NAME` for the profile, and `ornith-claude` for the default profile, which run Claude Code
  against the local model. Plain `claude` still uses your Anthropic account.
- applies a small fix to a copy of the model's chat template, so Claude Code's mid-conversation system messages
  work. The model file is not changed.

If you connect OpenCode, setup adds the model to your OpenCode config after backing the file up. In OpenCode it
appears under `/models` as "Ornith (local, ornith-launcher)". If the config has comments, which a rewrite would
lose, setup shows the entry for you to paste instead and asks before rewriting the file.

`./ornith-launcher setup --integrations NAME` connects a profile you set up earlier. See
[Using the model from other tools](#using-the-model-from-other-tools).

### Cleaning up

Setup records everything it creates: profiles, any desktop icon or menu entry, downloaded files and any
coding-tool entries. `./ornith-launcher uninstall` lists those records and removes what you choose.

## One-click launcher

The one-click launch needs a desktop icon or an applications menu entry. Both are optional, and setup asks about
them at the end:

| Question | Default | What it creates |
|---|---|---|
| Put a launch icon for this model on your desktop? | Yes | An icon in your desktop folder |
| Also add it to your applications menu? | No | An entry in `~/.local/share/applications` |

Setup only asks the first question when your desktop folder exists. When you skip the icon, the second question
becomes "Add a launcher for this model to your applications menu?". Each one is named "Ornith: NAME" after the
profile, so every model you set up gets its own. If you say no to both, nothing is added to your desktop or menu,
and you start the model with `./ornith-launcher launch NAME` or with `l` on setup's first menu. To add them later,
run `./ornith-launcher setup --shortcuts NAME`. It asks the same questions and skips any icon or entry the profile
already has.

Double-clicking the icon, or clicking the menu entry:

1. Opens the terminal you picked during setup: the system default, or one such as GNOME Terminal, Konsole, kitty,
   Alacritty or xterm.
2. Starts the model server with the profile's settings, fetching the llama.cpp image first if it is missing.
3. Shows the server's own log as it comes, with a status line every 15 seconds.

To stop the model, press Ctrl+C in that window. It asks "Stop the server and free its memory?" and defaults to
yes. Closing the window leaves the server running, and `./ornith-launcher stop` stops it later. If another model is
already running, the launcher asks whether to stop it first (default yes), because two large models rarely fit in
memory together.

On GNOME the launcher marks its own desktop icon as trusted, so it starts on the first double-click. Other
desktops may ask you to allow launching once. If you turned on the chat log, right-clicking the icon or menu entry
also offers "Show chat log", on desktops that show actions.

Both run `ornith-launcher launch NAME`, so the same command works from any terminal. On a machine without a
desktop, setup offers neither, and the command runs the server in the current terminal.

## Requirements

- Linux with Docker Engine, plus the NVIDIA Container Toolkit for NVIDIA GPUs.
- Python 3.9 or newer. The tool uses only the standard library, so there is nothing to install from PyPI.
- Disk space for the model, from about 5 GiB for a 9B at Q4_K_M to hundreds of GiB for a 397B, plus a few GB for
  the llama.cpp image.
- Memory for the model and its context, from about 10.5 GiB for a 9B at Q4_K_M with a 32k context and vision off
  (ESTIMATED).

`./ornith-launcher doctor` checks Docker, GPU access, disk space and the rest, and prints the exact command for
each problem it finds. The launcher never runs `sudo` itself. Supported GPUs and distributions are listed in
[docs/HARDWARE.md](docs/HARDWARE.md).

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
   and the optional desktop icon and applications menu entry. Enter takes the default each time.

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

### OpenCode

Pick "Ornith (local, ornith-launcher)" under `/models`. Uninstall removes only the entry setup added, and never
entries you edited yourself.

## Safety and privacy

- The server binds to `127.0.0.1` only, so other machines on your network cannot reach it.
- Downloads are pinned and checked: the llama.cpp image by digest, model files by sha256.
- The launcher never runs `sudo`. When something needs root, such as adding you to the `docker` group, it prints
  the command for you to run.
- It asks before installing software, deleting files or changing another tool's config, and backs up a config
  file before changing it.
- It never moves, copies or deletes models that belong to other apps.
- It sends no telemetry. It connects only to Hugging Face, ghcr.io for the llama.cpp image and, if you choose
  them, the official Claude Code and OpenCode installers.
- Ctrl+C stops every long-running command cleanly, including the containers and processes it started.

## Removing things

`./ornith-launcher uninstall` offers:

- "Remove one setup" or "Remove all setups". For each setup you then choose the setup itself (its profile, launch
  icons, coding-tool entries and the model it downloaded), the model server (the llama.cpp image), or both.
- "Remove unfinished downloads" deletes the downloads you stopped and keeps finished models.
- "Remove everything" deletes all setups, every model it downloaded, the llama.cpp image it pulled and its own
  settings.

It removes only what the launcher's own records list. Models it found elsewhere on the machine are never touched,
and the llama.cpp image is not offered if it was there before setup or another setup still uses it.
`./ornith-launcher remove NAME` removes a single profile and asks before deleting downloaded files.

## What has been tested

- Machine: NVIDIA DGX Spark (GB10, 128 GB unified memory, arm64) with Ubuntu 24.04.
- Model: Ornith 1.5 35B-A3B. Its results are in [docs/BENCHMARKS.md](docs/BENCHMARKS.md), each labelled
  TESTED, INFERRED or ESTIMATED.
- Untested: other Linux distributions, discrete NVIDIA, AMD and Intel GPUs, CPU-only machines, and every other
  model and size. Plans for them are ESTIMATED from the GGUF headers and the memory model, and the 397B is also
  marked UNTESTED LOCALLY.

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
| `ornith-launcher doctor` | Check Docker, the GPU, disk, the image, profiles and tools (read-only) |
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
| `--models-dir PATH` | Folder for downloaded models (default `~/.ornith-launcher/models`) |
| `--port PORT` | Port for the server (default 18400, or the next free one) |
| `--name NAME` | Profile name (default: made from the model and context) |
| `--plain` | Numbered menus instead of full-screen lists |
| `--yes` | Accept every default without asking |
| `--accept-uncensored` | Accept an uncensored model with `--yes`; `--yes` alone never does |
| `--repair NAME` | Put back what a profile needs: the image, a missing download, a changed model file |
| `--integrations NAME` | Connect an existing profile to Claude Code or OpenCode |
| `--shortcuts NAME` | Add a desktop icon or applications menu entry for an existing profile |

`launch`:

| Option | What it does |
|---|---|
| `--here` | Run in this terminal instead of opening a window |
| `--no-monitor` | Start the server and return |
| `--chat-log` | Open the chat log window this time |
| `--no-chat-log` | No chat log this time |

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
| `status` | `--command` | Also show the docker command |
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
| Downloaded models | `~/.ornith-launcher/models`, or the folder you pick |
| Claude Code wrappers | `~/.local/bin/ornith-claude` and `~/.local/bin/ornith-claude-<profile>` |
| Desktop icon and menu entry | Your desktop folder and `~/.local/share/applications` |

| Environment variable | What it does |
|---|---|
| `HF_TOKEN` | Sent to huggingface.co for downloads, if you set it; the launcher never asks for one |
| `HF_ENDPOINT` | Use a Hugging Face mirror |
| `HF_HOME`, `HF_HUB_CACHE` | Where the quick search looks for the Hugging Face cache |
| `LLAMA_CACHE`, `OLLAMA_MODELS` | Where it looks for llama.cpp and Ollama models |
| `XDG_CONFIG_HOME`, `XDG_DATA_HOME` | Where settings and menu entries go |

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
