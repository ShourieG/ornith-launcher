# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- POSIX `sh` entry script that finds Python 3.9+ and runs the package from a plain checkout.
- Python package with a `--version` command.
- Pinned llama.cpp build b11552: Docker image digests and native release binary checksums in `data/pins.json`.
- Unit tests, and CI on Linux with ruff and shellcheck (macOS and Windows jobs ready but switched off).
- Licence (Apache-2.0), notice, disclaimer, security policy and contributing guide.
- `scan` command: checks the machine's memory, GPU and Docker setup, then lists the Ornith models that fit
  (suggested list, or every combination with `--full`) with a memory estimate for each. `--refresh` rescans
  Hugging Face without a login.
- A shipped list of Ornith GGUF builds on Hugging Face (metadata only), so `scan` works offline.
- Ornith 1.0 and 1.5 at 9B, 35B and 397B, and 1.5 at 122B, recognised from their GGUF headers.
- `setup` command, up to a downloaded or found model: system check, a quick search for Ornith models already on
  the machine (Hugging Face cache, LM Studio, Ollama, llama.cpp cache, `~/models`, `~/Downloads`), the model
  list, options, an editable models folder, and a resumable, sha256-verified download. Models found on the
  machine are used where they are and never moved. Full-screen lists where the terminal allows, numbered menus
  with `--plain`.
- Launching: `setup` now finishes with the llama.cpp image, llama.cpp's own memory check, a terminal for the
  server monitor, an optional desktop shortcut and a saved profile. `launch`, `stop`, `status`, `list`,
  `monitor`, `remove` and `uninstall` commands; `ornith-launcher` alone runs setup the first time and launches the
  default profile after that. The server listens on 127.0.0.1 only, and Ctrl+C stops it (default yes).
- `setup --repair NAME` puts back a missing image or download.
- `doctor` command: a read-only check of Docker, GPU access, disk space, the llama.cpp image, the catalog, every
  profile's files and the optional tools, each problem with its fix.
- Claude Code and OpenCode: setup can connect a profile to either or both, installing them with their official
  installers when asked. Claude Code runs through `ornith-claude-<profile>` (and `ornith-claude` for the default
  profile) without touching your own Claude Code settings, with a chat-template fix applied to a copy so system
  messages in mid-conversation work. OpenCode gets a provider entry; the config file is backed up first, and a
  file with comments is never rewritten without asking. `setup --integrations NAME` changes this later.
- `bench` command: the multi-needle long-context test and llama.cpp perplexity, with a pause whenever the GPU
  reaches 80 °C until it cools to 60 °C. Results go to a local JSONL file with the machine, build, image and
  model checksum. `docs/BENCHMARKS.md` lists the DGX Spark results, each labelled TESTED, INFERRED or ESTIMATED,
  and is generated from `data/tested.json`.
