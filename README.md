# ornith-launcher

Unofficial tool for running Ornith models locally with llama.cpp. It is not affiliated with Ornith AI or any
other company named here; see [NOTICE](NOTICE) and [DISCLAIMER.md](DISCLAIMER.md).

> Early development. Linux with Docker only for now; Windows and macOS are planned.

ornith-launcher checks your machine's memory and GPU, then suggests an Ornith model (any version and size), a
quantization and a context size that fit. It downloads the model from Hugging Face (or uses one you already have)
and starts it with llama.cpp. It can also connect the model to Claude Code and OpenCode.

## Requirements

- Python 3.9 or newer. Nothing else to install from PyPI.
- Linux: Docker Engine (with the NVIDIA Container Toolkit for NVIDIA GPUs).
- Tested on Ubuntu 24.04 (DGX Spark). Other distributions with Python 3.9+ and Docker Engine should work but are
  untested; see [docs/HARDWARE.md](docs/HARDWARE.md#linux-distributions).

## Quick start

```
git clone https://github.com/ShourieG/ornith-launcher.git
cd ornith-launcher
./ornith-launcher
```

`./ornith-launcher` opens the setup: it checks the machine, looks for Ornith models you already have, lets you
pick one (or a download), and saves a profile with a launch icon on your desktop. Once a model is set up, the same
menu has "Launch a model" and "Uninstall". Running the launcher never starts a server by itself.

| Command | What it does |
|---|---|
| `./ornith-launcher scan` | What fits this machine, read-only (`--full` for every combination) |
| `./ornith-launcher setup` | Add a profile (`--model PATH` to use a file you have) |
| `./ornith-launcher launch [NAME]` | Start a profile's server and watch it; Ctrl+C asks whether to stop it |
| `./ornith-launcher logs [NAME]` | Show a server's own log live; Ctrl+C closes only the log |
| `./ornith-launcher chat-log [NAME]` | Show requests to a model and its replies live, when the chat log is on (`--window` opens it in its own window) |
| `./ornith-launcher stop [NAME]` | Stop a server and free its memory |
| `./ornith-launcher status` | What is running, and where models are stored |
| `./ornith-launcher list` | Profiles |
| `./ornith-launcher doctor` | Check Docker, the GPU, disk, the image, profiles and tools (read-only) |
| `./ornith-launcher setup --integrations NAME` | Connect a profile to Claude Code or OpenCode |
| `./ornith-launcher remove NAME` | Remove a profile (asks before deleting downloaded files) |
| `./ornith-launcher uninstall` | Remove one setup, all setups, unfinished downloads, or everything the launcher created (also in the setup menu) |
| `./ornith-launcher bench NAME` | Long-context and perplexity tests (long, hot GPU runs) |

The server listens on 127.0.0.1 only. Models found on your machine are used where they are and never moved or
deleted.

The server window shows the server's own log as it comes, with a status line every 15 seconds. Setup can also
give a model a chat log: every launch then opens a second window that shows each request sent to the model and
its reply as it streams. A small proxy on the model's port (127.0.0.1 only) passes everything through, so clients
keep the same URL, and keeps the last 10 requests in memory only, never on disk. Ctrl+C or closing the chat log
window closes only that window, pressing `c` and Enter in the server window opens it again, and stopping the
server closes it too. `launch --chat-log` or `--no-chat-log` changes this for one launch.

With Claude Code connected, run `ornith-claude` (or `ornith-claude-<profile>`) instead of `claude`; plain `claude`
keeps using your Anthropic account. Anthropic does not support Claude Code with non-Claude models; see
[DISCLAIMER.md](DISCLAIMER.md).

Benchmark results so far: [docs/BENCHMARKS.md](docs/BENCHMARKS.md). Supported hardware:
[docs/HARDWARE.md](docs/HARDWARE.md). When something goes wrong: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).
Licences and trademarks: [docs/LEGAL.md](docs/LEGAL.md). Credits: [CREDITS.md](CREDITS.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: see [SECURITY.md](SECURITY.md).

## Licence

Apache-2.0, see [LICENSE](LICENSE). Models are not part of this project and keep their own licences.
