# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-30

The first release, for Linux and macOS. Windows support is planned for a later release.

### Added

- `./ornith-launcher` opens setup, which takes you from a fresh machine to a running Ornith model. It checks the
  memory, the GPU and Docker (on Linux) or the GPU memory limit Metal sets (on macOS), suggests builds that fit
  with the reason for each, and writes the llama.cpp settings itself. Enter takes the recommended choice at every
  step, Esc goes back, and a summary shows the model, settings, memory and download before anything is fetched.
  Setup works from fixed rules and needs no AI model, API key or account.
- A quick search for Ornith models already on the machine (the Hugging Face cache, LM Studio, Ollama, the
  llama.cpp cache, `~/models` and, except on macOS, `~/Downloads`), plus any path you give. A found model is used
  where it is and never moved, copied or deleted.
- A shipped catalog of the Ornith GGUF builds on Hugging Face (metadata only), so setup and `scan` work offline;
  `scan --refresh` updates it without a login. Ornith 1.0 and 1.5 at 9B, 35B, 122B and 397B are recognised from
  their GGUF headers, and vision files whatever their name.
- Model lists: a few suggestions, a best fit list (`b`) of what can run on this machine, and All models (`a`),
  with sorts, filters, search and a link to each model card. Every row says whether it fits, is a stretch or is
  too big, and carries TESTED, INFERRED or ESTIMATED. A stretch or too-big choice asks first and stays possible.
- Uncensored community models are hidden until you opt in (best effort, from names, tags and lineage), and
  choosing one needs the typed words "I understand". Models with no licence or a custom one ask for a
  confirmation.
- Contexts from 32k to 1M tokens, each with the memory it needs and a verdict. Past the native 262k the launcher
  sets up YaRN (x2 for 524k, x4 for 1M).
- A Settings menu for the KV cache, parallel requests, the vision file and the port, each with its memory.
- Downloads from Hugging Face without a login, pinned to a revision and checked by sha256. A server address that
  does not answer within 10 seconds is skipped for the next one, and tried last for the rest of the download.
  Ctrl+C keeps a stopped download to resume later with the same settings, or deletes it. The default models folder
  is `~/models/ornith-launcher`, and setup lets you pick another.
- On Linux the server runs in Docker from a llama.cpp b11552 image pinned by digest (CUDA, ROCm, Vulkan, Intel
  or CPU). On macOS it runs the llama.cpp b11552 release build without Docker, pinned by sha256 and unpacked only
  after every archive entry is checked, with its official GitHub release address and a progress line shown while
  it downloads, and `caffeinate` keeping the Mac awake. Servers listen on 127.0.0.1 only, and llama.cpp's own
  memory check runs before the first launch.
- A server window with the server's log and a status line, where Ctrl+C asks before stopping the server. An
  optional desktop icon and applications menu entry start a model with a double-click (`.desktop` files on Linux,
  marked trusted on GNOME; on macOS small apps built with `osacompile`, which open the chosen terminal directly
  and show in Launchpad and Spotlight, with `.command` files as the fallback), and `setup --shortcuts NAME` adds
  them later.
  On macOS, iTerm2 windows open through iTerm2's AppleScript after one Automation question, so iTerm2 does not
  ask "OK to run" for every window.
- An optional chat log window that shows each request and the reply as it streams, through a small proxy on the
  model's port. It keeps the last 10 requests in memory and writes nothing to disk.
- Claude Code and OpenCode connections, each optional, with the official installers offered only after a yes.
  Setup records a tool it installs, names its command (`claude`, `opencode`), checks that a new terminal window
  finds it and otherwise prints the PATH line, says beforehand when its installer may sit silent, shows a spinner
  with the time so far whenever the installer prints nothing, and goes on with its questions on a fresh screen
  afterwards. Claude Code gets `ornith-claude` and `ornith-claude-NAME` wrappers, with a fix applied to a copy of
  the chat template so mid-conversation system messages work, and with `CLAUDE_CODE_AUTO_MODE_SERVER=0`, since
  llama.cpp cannot run auto mode's server-side checks; your own Claude Code settings are not changed. OpenCode
  gets a provider entry after a backup of its config, declaring image input when vision is on, marking the model
  as a reasoning model with a "no-thinking" variant, and, if you agree, making it the default model. Setup also
  adds a short `show-pictures.md` to OpenCode's instructions so models can draw charts in the chat, and says so
  before you pick OpenCode. Setup can open the connected tools in their own windows at each launch (the default).
- Commands: `setup` (with `--repair`, `--integrations` and `--shortcuts`), `launch`, `stop`, `status`, `list`,
  `monitor`, `logs`, `chat-log`, `scan`, `doctor`, `remove`, `uninstall` and `bench`. `doctor` is read-only and
  prints the fix for each problem it finds.
- `uninstall` removes one setup or all setups (each with or without their models and the llama.cpp server),
  unfinished downloads, the coding tools setup installed (their own settings and history only if you ask), or
  everything the launcher created. It removes only what the launcher recorded as its own, including a setup's
  server log on macOS, and puts back OpenCode's previous default model unless you picked another one since. Each
  question starts on a fresh screen, and after a removal setup waits for Enter before going back to its menu.
- `bench`: a long-context retrieval test and llama.cpp perplexity, pausing when the GPU reaches 80 °C (Linux).
  `docs/BENCHMARKS.md` holds the DGX Spark results, each labelled TESTED, INFERRED or ESTIMATED.
- Documentation: the README gives an overview, what has been tested and a quick start, and `docs/GUIDE.md`
  describes every screen, key, option and file, with the Linux and macOS details, and how to weigh model size,
  quant and context against each other, with links to the published studies behind it.
- A first-run disclaimer, shown again when its version changes; GPL-3.0-or-later licence with section 7 terms in
  `NOTICE` (keep the attribution, mark modified versions, no rights in the name); credits, security policy and
  contributing guide (DCO sign-off). CI runs the unit tests on Linux and macOS with Python 3.9 and the latest
  Python, plus ruff and shellcheck.

[Unreleased]: https://github.com/ShourieG/ornith-launcher/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ShourieG/ornith-launcher/releases/tag/v0.1.0
