# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- POSIX `sh` entry script that finds Python 3.9+ and runs the package from a plain checkout. It also finds a newer
  Python installed alongside an old `python3` (`python3.9` to `python3.14`, for Ubuntu 20.04 and RHEL 8), and
  names the old version when there is none.
- Python package with a `--version` command.
- Pinned llama.cpp build b11552: Docker image digests and native release binary checksums in `data/pins.json`.
- Unit tests, and CI on Linux with ruff and shellcheck (macOS and Windows jobs ready but switched off).
- Licence (Apache-2.0), notice, disclaimer, security policy and contributing guide.
- `docs/HARDWARE.md` lists Linux distributions: Ubuntu 24.04 tested, others expected to work but untested.
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
  server monitor, a launch icon on the desktop (asked, default yes), an applications menu entry only when asked,
  and a saved profile. `launch`, `stop`, `status`, `list`, `monitor`, `remove` and `uninstall` commands.
  `ornith-launcher` alone opens setup and never starts a server by itself; setup's first menu has "Launch a model"
  once a setup exists. The server listens on 127.0.0.1 only, and Ctrl+C stops it (default yes).
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
- Browsing models in `setup`: an "only uncensored" list, behind the same opt-in as showing uncensored models and
  with the uncensored warning above it. The uncensored label is a guess from names and tags, as the disclaimer
  says.
- Full-screen lists link each model to its Hugging Face model card (Ctrl+click, or Cmd+click on macOS), and the
  highlighted model's URL is printed too, for terminals such as Konsole that only follow plain URLs there. Pages
  turn with the arrow keys as well as `n` and `p`.
- Browsing models in `setup`: Download size, Likes and 30-day downloads columns next to Memory (likes and downloads
  are per Hugging Face repo), and a Sort action (`s`) for the long lists: memory or download size either way,
  likes or downloads most first; models that fit stay first. Search is `/`. Catalog refreshes now update
  likes and downloads for unchanged repos too.
- Back (`b`, or Esc in full-screen lists) and Main menu (`m`) in the setup menus up to the summary, so a model
  can be changed from the context step. Back from the context step returns to the same list and page. On the
  first menu, browse and download is `d`. Each setup step opens on a fresh screen.
- The server window starts with how to stop it: Ctrl+C stops the server and unloads the model, and closing the
  window leaves it running (with the command to stop it later).
- Claude Code and OpenCode list the model by its own name with quant and context, and with `[UNCENSORED]` when it
  is, for example "Ornith-1.5-9B-uncensored Q8_0 [UNCENSORED], 262k context (local)".
- Uninstall menu (`uninstall`, or Uninstall on setup's first menu once something is installed): remove one setup,
  all setups, or everything. It removes only what ornith-launcher recorded as its own in `installed.json` and
  the profiles: downloaded models (with partial downloads), desktop icons, menu entries and the llama.cpp image
  it pulled (only its own reference, so other tags keep the image). Models found elsewhere are never touched.
- Removing one setup asks what to remove: the setup with its launch icon and menu entry (and the model, if the
  launcher downloaded it), the model server (llama.cpp image), or both. The model server is offered only when
  the launcher pulled it and no other setup needs it.
- `launch` offers to download a missing model server instead of refusing to start.
- Stopped downloads: Ctrl+C during a download asks whether to keep it to resume later (the default) or delete it
  and exit. Setup's first menu says when a download was stopped, and Resume a download (`r`) lists each with its
  progress and continues it in the same folder with the settings chosen when it started (context, KV cache,
  parallel requests, vision, port and the confirmations given), without asking for them again. Uninstall gets
  Remove unfinished downloads, and lists only the options that apply instead of greyed-out ones that did nothing
  when chosen.
- Yes/no questions name their default after the hint, for example `[Y/n] (default: yes)`. "Launch now?" at the
  end of setup defaults to no, so Enter alone never starts a server.
- Uninstall lists the coding-tool settings it removes with a setup: the OpenCode model entry (with the file it is
  in) and the Claude Code wrapper, both in the parts to pick and on the confirmation screen.
- The server window shows the server's own log as it comes, between status lines every 15 s, leaving out the
  launcher's own health and metrics checks. New `logs` command for the same log in any terminal.
- Chat log: setup asks (default no) whether to open a chat log window at every launch. It shows each request sent
  to the model (its newest message, not the whole history) and the reply as it streams: thinking, answer and
  tool calls, then tokens and speed. A small proxy on the profile's port (127.0.0.1 only) passes every request
  through unchanged, so clients keep the same URL, and keeps the last 10 requests in memory only, never on disk.
  Ctrl+C or closing the window closes only the window; `c` and Enter in the server window opens it again, closing
  an old one that is still open. The desktop icon and menu entry get a "Show chat log" action. New `chat-log`
  command, and `launch --chat-log` and `--no-chat-log` for one launch.
- Stopping a server (Ctrl+C in the server window, `stop`, `remove`, uninstall) also ends its chat log proxy and
  closes the chat log window.
- Downloads say what each file is (the model, each of its parts, or the vision file that lets the model read
  images), the total size and folder, how much of a resumed file is already there, and how long each file took.
  The Settings and Summary screens give the vision file's size, and the vision question says it adds that
  download. A paused download says how far it got and how to resume or remove it.
- The uncensored label also catches "obliterated", "abliterix", "decensored", "unaligned", "unfiltered",
  "jailbreak", "NSFW" and "amoral" names, and looks at the models a file was made from: Hugging Face base models,
  and the fine-tune, tags and base models in the GGUF header, which also covers models found on disk.
- Showing uncensored models is a saved choice, and the opt-in screen, the disclaimer and the list titles
  ("uncensored shown") now say so.
- The disclaimer calls hiding uncensored models best effort, not a content filter: read a community model's card
  (linked in the lists) before using it, and report unlabelled uncensored models as issues.
