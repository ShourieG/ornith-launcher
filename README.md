# ornith-launcher

An unofficial command-line tool that picks, downloads and runs Ornith models on your own machine with llama.cpp.
It is not affiliated with Ornith AI or any other company named here; see [NOTICE](NOTICE) and
[DISCLAIMER.md](DISCLAIMER.md).

> Early development. Linux with Docker only for now; Windows and macOS are planned.

Getting a model running locally means choosing a quant that fits in memory and a context size you can afford,
finding or downloading the GGUF file, and getting the llama.cpp settings right. ornith-launcher works these out
for your machine. It checks how much memory and GPU you have, shows the Ornith builds that fit with how much
memory each one needs, then uses the copy you already have or downloads the one you pick, and starts it.

Setup ends with a profile and an icon on your desktop. Double-clicking the icon opens a terminal and starts the
model server; Ctrl+C in that window asks whether to stop it. If you use Claude Code or OpenCode, setup can connect them to the
model as well.

Models already on your disk are used where they are. The launcher looks in the Hugging Face cache, LM Studio,
Ollama, the llama.cpp cache, `~/models` and `~/Downloads`. Downloads come from Hugging Face, are checked
against their sha256 hash and pick up where they left off if you stop them. The server runs in Docker from a
llama.cpp image pinned by digest and listens on `127.0.0.1` only. Setup records everything it creates, so
`ornith-launcher uninstall` can remove all of it later.

If you want more control, the model lists sort and filter every build the catalog knows about, community uploads
included, and the context goes up to 1M tokens. The KV cache, the number of parallel requests, vision and the
port are settings you can change too. A model that is too big for your machine still appears in the lists, and
picking it shows a warning first.

## Requirements

- Python 3.9 or newer. There is nothing to install from PyPI.
- Docker Engine, with the NVIDIA Container Toolkit for NVIDIA GPUs. `ornith-launcher doctor` checks both.
- Disk space for the model, from about 5 GiB for a 9B at Q4_K_M to hundreds of GiB for a 397B.
- Tested on Ubuntu 24.04 (DGX Spark). Other distributions with Python 3.9+ and Docker Engine should work but are
  untested; see [docs/HARDWARE.md](docs/HARDWARE.md#linux-distributions).

## Quick start

```
git clone https://github.com/ShourieG/ornith-launcher.git
cd ornith-launcher
./ornith-launcher
```

The first run opens setup. Accept the disclaimer, press `d` to browse, and press Enter on the first suggestion.
After that, Enter takes the recommended choice at each step, and the summary shows the download size and time
before anything is downloaded. When setup finishes you have a profile, a launch icon on your desktop and, if you
chose them, Claude Code and OpenCode set up for the model. From then on:

| To | Do this |
|---|---|
| Start the model | Double-click its desktop icon, or run `./ornith-launcher launch` |
| Stop it | Press Ctrl+C in its window, or run `./ornith-launcher stop` |
| Use it from Claude Code | Run `ornith-claude` instead of `claude` |
| Add another model | Run `./ornith-launcher` again and press `d` |
| Remove things | Run `./ornith-launcher uninstall` |

Running `./ornith-launcher` with no command always opens setup. It never starts a server by itself; once a
model is set up, the first menu also offers Launch a model (`l`) and Uninstall (`u`).

## Setup, step by step

Setup runs in full-screen lists in a terminal, or numbered menus with `--plain` (also used for pipes and SSH).

| Step | What you see | What you can do |
|---|---|---|
| First menu | Ornith models already on this machine, and any stopped download | Use a found model, give a path (`p`), browse and download (`d`), resume a download (`r`), launch (`l`), uninstall (`u`) |
| Suggested | A few picks for this machine, each with the reason it was picked | Open the best fit list (`b`), All models (`a`) or search (`/`) |
| Best fit list | Every file that can run here, each at its recommended setting, best first | Sort, filter, search, or switch to All models with `a` |
| All models | Every file in the catalog at the least memory it needs, newest and largest first | Sort, filter, search, or switch to the best fit list with `b` |
| Context size | Each context from 32k to 1M with its memory, what is left in the plan, what is really free, and a verdict | Pick a context; the recommended one is highlighted |
| Settings | Context, KV cache, parallel requests, vision and port, with the memory they need | Change any of them, then Continue |
| Models folder | Where the download goes and how much space is free | Keep the folder or type another |
| Summary | The model, settings, memory, download size and time | Go ahead, or Esc to go back |
| Finishing | The download, then the coding tools, terminal, chat log and desktop icon | Answer each question; Enter takes the default |

Each list row says whether it fits: yes, stretch (into the 15% of memory kept free, or more than is free right
now) or too big. Picking a stretch or too-big setting asks first. The highlighted row shows its file, the reason
for its verdict, its badges (TESTED, INFERRED, ESTIMATED, UNTESTED LOCALLY, LOW-BIT and others) and a link to its
model card.

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

### Sorts and filters

The best fit list sorts by best first or best for small memory (a combined score of memory left, quant, size,
context, popularity and trust, labelled as a heuristic). Both lists also sort by memory, download size, quant
(by bits per weight, so NVFP4, MXFP4, IQ and UD quants sort on one scale), likes, downloads in the last 30 days,
lifetime downloads and last update. All models adds model order and model size.

Filters apply to both lists, and each shows how many files match and how many fit:

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

Uncensored models stay hidden until you turn them on, which shows a warning first, and choosing one means typing
"I understand". The label is a best-effort guess from names, tags and lineage; see [DISCLAIMER.md](DISCLAIMER.md).
Sorts and filters last until setup ends.

## Running a model

A profile is saved for every setup, and the first one becomes the default. The desktop icon runs
`ornith-launcher launch <profile>` in the terminal you picked during setup (the system default, or any installed
one such as GNOME Terminal, Konsole, kitty, Alacritty or xterm).

The server window shows the server's own log as it comes and a status line every 15 seconds. Ctrl+C asks
"Stop the server and free its memory?" and defaults to yes; closing the window leaves the server running, and
`ornith-launcher stop` stops it later. The server listens on `127.0.0.1` at the profile's port (18400, or the next
free one), with an OpenAI-compatible API.

Setup can also give a model a chat log. Every launch then opens a second window that shows each request sent to
the model and its reply as it streams: thinking, answer, tool calls, tokens and speed. A small proxy on the
model's port passes everything through, so clients keep the same URL. It keeps the last 10 requests in memory and
writes nothing to disk. Closing the chat log window closes only that window; press `c` and Enter in the server window
to open it again. The desktop icon gets a "Show chat log" action too.

With Claude Code connected, run `ornith-claude` (the default profile) or `ornith-claude-<profile>` instead of
`claude`; plain `claude` keeps using your Anthropic account. Setup applies a small chat-template fix for Claude
Code. Anthropic does not support Claude Code with non-Claude models; see [DISCLAIMER.md](DISCLAIMER.md). For
OpenCode, setup adds an entry for the model to your OpenCode config and backs the file up first. Setup asks before
installing either tool.

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

`bench` pauses when the GPU runs hot. Results so far, each labelled TESTED, INFERRED or ESTIMATED, are in
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

The launcher sends no telemetry. It connects only to Hugging Face, ghcr.io for the llama.cpp image, and, if you
choose them, the official Claude Code and OpenCode installers.

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
