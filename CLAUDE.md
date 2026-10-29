# CLAUDE.md

Guidance for AI coding agents and human contributors working in this repository. The tool itself must work
without any AI assistance; this file only helps people and agents who develop it.

## Project

`ornith-launcher` is an unofficial command-line tool that sets up and launches Ornith models locally with
llama.cpp. It is licensed under GPL-3.0-or-later.

## Setup

The tool uses only the Python standard library, so running or testing it needs no install step.

- Python 3.9 or newer.
- Optional, for local linting: `python3 -m venv .venv && .venv/bin/pip install ruff` (or `pipx install ruff`).
- Integration tests also need Docker, and some need a GPU and network access.

## Commands

```
./ornith-launcher                                                    # run the tool
python3 -m unittest discover -s tests -t .                           # unit tests (offline, fast)
ORNITH_IT=1 python3 -m unittest discover -s tests/integration -t .   # integration tests (Docker, GPU, network)
ruff check . && ruff format --check .                                # lint and format check
shellcheck ornith-launcher                                           # entry script check
```

Run the unit tests and ruff before every commit.

## Code rules

- The runtime uses the Python 3.9+ standard library only, with no third-party dependencies. Dev-only tools such as
  ruff and shellcheck are fine.
- The entry script `ornith-launcher` is POSIX `sh`, not bash, because macOS ships bash 3.2.
- Keep code portable across Linux, macOS and Windows: use `pathlib`, never hard-code path separators, and avoid
  `curses`, `os.pwrite` and POSIX-only calls outside the platform helpers.
- Use the `src/` layout and type hints with `from __future__ import annotations`. Avoid global state.
- Keep pure logic (parsing, calculations, argument building, config merging) free of side effects and unit-tested.
  Put network, process and file-system side effects behind small interfaces that tests can fake.
- Every user-facing error says what is wrong and gives the exact command or step that fixes it.
- Match the surrounding code's style, naming and comment density.

## Safety and legal rules

- Always describe the project as unofficial. Use no third-party logos, and use third-party names only to describe
  compatibility.
- Never commit model weights, third-party chat-template text, test corpora or other third-party content. Ship
  transformation rules or hashes instead.
- Label every benchmark number TESTED, INFERRED or ESTIMATED, and never present an estimate as measured.
- Pin external downloads (container images, binaries, model files) by digest or sha256 and verify them. Changing a
  pin needs review.
- Servers bind to `127.0.0.1` only.
- Never run `sudo` from code; print the command for the user instead.
- Every long-running command stops cleanly on Ctrl+C, including any containers or processes it started.
- Ask the user before installing software, editing files outside the tool's own directories, deleting user files,
  or editing other tools' configs. Back up any config file before changing it.

## Tests

- Unit tests use `unittest`, run offline, and live in `tests/`. Fixtures go in `tests/fixtures/`.
- Integration tests live in `tests/integration/` and skip unless `ORNITH_IT=1` is set.
- `tests/__init__.py` puts `src/` on the import path, which is why the commands pass `-t .`.
- New behaviour needs a test, and a bug fix needs a test that fails without the fix.
- Use made-up uploader and repository names in tests (for example `quant-uploader/`), never real accounts.

## Commits and pull requests

- Sign off every commit (DCO) with `git commit -s`. There is no CLA.
- Write a short imperative subject line (72 characters at most), then a body explaining why.
- Keep one logical change per commit and keep pull requests focused.
- Record user-visible changes in `CHANGELOG.md` (Keep a Changelog format).
- Never commit secrets, tokens, local paths or machine-specific config.
- `ideate/` is a local, git-ignored scratch area. Never commit or force-add it, and never make committed code,
  tests or CI depend on it.

## For AI agents

- Don't launch long GPU jobs, benchmarks or model downloads yourself; give the user the command to run.
- Don't create remote repositories, push, or publish anything without the user's explicit approval.
- When a decision is not covered by the code or this file, ask the user rather than guessing.
