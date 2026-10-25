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
