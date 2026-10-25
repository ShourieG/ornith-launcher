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

## Quick start

```
git clone https://github.com/ShourieG/ornith-launcher.git
cd ornith-launcher
./ornith-launcher
```

The first run starts the setup: it checks the machine, looks for Ornith models you already have, lets you pick
one (or a download), and saves a profile. Later runs start the default profile.

| Command | What it does |
|---|---|
| `./ornith-launcher scan` | What fits this machine, read-only (`--full` for every combination) |
| `./ornith-launcher setup` | Add a profile (`--model PATH` to use a file you have) |
| `./ornith-launcher launch [NAME]` | Start a profile's server and watch it; Ctrl+C asks whether to stop it |
| `./ornith-launcher stop [NAME]` | Stop a server and free its memory |
| `./ornith-launcher status` | What is running, and where models are stored |
| `./ornith-launcher list` | Profiles |
| `./ornith-launcher setup --integrations NAME` | Connect a profile to Claude Code or OpenCode |
| `./ornith-launcher remove NAME` | Remove a profile (asks before deleting downloaded files) |
| `./ornith-launcher uninstall` | Undo everything the launcher created |

The server listens on 127.0.0.1 only. Models found on your machine are used where they are and never moved or
deleted.

With Claude Code connected, run `ornith-claude` (or `ornith-claude-<profile>`) instead of `claude`; plain `claude`
keeps using your Anthropic account. Anthropic does not support Claude Code with non-Claude models; see
[DISCLAIMER.md](DISCLAIMER.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: see [SECURITY.md](SECURITY.md).

## Licence

Apache-2.0, see [LICENSE](LICENSE). Models are not part of this project and keep their own licences.
