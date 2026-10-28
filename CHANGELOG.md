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
- `setup --shortcuts NAME` adds a desktop icon or applications menu entry to a profile set up earlier. It asks
  the same two questions as setup and skips any the profile already has.
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
- Full-screen lists link each model to its Hugging Face model card (Ctrl+click, or Cmd+click on macOS), and the
  highlighted model's URL is printed too, for terminals such as Konsole that only follow plain URLs there. Pages
  turn with the arrow keys as well as `n` and `p`.
- Browsing models in `setup`: after Suggested come two lists, the best fit list (`b`) and All models (`a`). The
  best fit list is advice: only what can run on this machine (fits or stretch), each file at its recommended
  setting, best first. All models is the catalog: every file at the least memory it can run with (32k, vision off,
  in a "Needs at least" column), newest version and largest model first, and fit never moves or hides a row there.
  The best fit list says how many matching files are too big and that `a` shows them. Picking a row in All models
  starts the next screens from the file's recommended setting, and a stretch or too-big file asks first.
- Columns: Download size, Likes, downloads in the last 30 days and in all ("DLs/30d", "DLs total", both per
  Hugging Face repo) and Updated (when the repo last changed), next to Memory and Fits.
- Sort (`s`): each list keeps its own. The best fit list offers best first and "best for small memory" (advice,
  what fits on top), then the plain orders; All models offers model order, model size, memory needed, download size
  and quant either way, likes, downloads (30 days), downloads (lifetime) and updated. Plain orders go by their
  value alone. The quant sort goes by bits per weight, so NVFP4, MXFP4, IQ and UD quants of any model size sort on
  one scale.
- Filters (`f`), shared by both lists, with a live count of matching files and how many fit: model size, version
  and quant group (tick any number), source, licence, vision, updated in the last 7 to 365 days, likes, downloads
  in the last 30 days, downloads in all, and Uncensored (hidden, shown, or only uncensored models, behind the same
  warning and saved opt-in as before; "only" lasts until setup ends). Every change applies at once and the picker
  says how many files now match; Esc goes back with it kept, and `c` clears that filter. Search (`/`) narrows the
  list you are in and never reorders it; from Suggested it opens All models. `c` clears the search and filters.
  Every list names its filters and search under its title. Sorts and filters last until setup ends.
- Catalog refreshes update likes and both download counts for unchanged repos too. The scan names the listing
  fields it reads, since Hugging Face only gives the lifetime count that way. The shipped list has the counts
  of 2026-10-27.
- Settings after the context screen is a menu: Context, KV cache, Parallel requests, Vision and Port, then Continue
  in bold below a blank line (highlighted, so Enter alone goes on). Enter opens a setting; KV cache, Parallel
  requests and Vision show each option's memory, fit and free memory, and one that makes the setting a stretch or
  too big asks first. The memory line above the menu follows every change. Esc goes back to the context screen;
  Context opens it too and comes back. The port must be from 1024 to 65535 and free. Before, "Use these settings?
  No" went through four questions with no way back.
- Under each list, the highlighted row's details are on separate lines with bold labels (File, Why, Too big or
  Stretch, Score, Badges, Notes), wrapped to the window, then a blank line and "URL - <model card>". The plain
  menus show the same block under each row. The space they take stays the same as you move, so the list does not
  jump.
- Back (Esc; in plain menus, Esc then Enter, or type esc) and Main menu (`m`) in the setup menus up to the summary,
  so a model can be changed from the context step. Back from the context step returns to the same list and page. On
  the first menu, browse and download is `d`. Each setup step opens on a fresh screen.
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
- The context screen shows this machine's memory (total, the system's share, what is usable, the plan's limit,
  the 15% reserve and what is free right now) and the model weights, then per context the memory it adds on top
  (KV cache and compute buffers), the total, three memory columns and a verdict: recommended, fits, stretch or too
  big. Plan left is the room before the reserve, Reserve used is how much of the reserve a setting takes ("none",
  "5.5 of 17.3 GiB", "all 17.3 GiB"), and Free after is what the machine really has left ("1.9 GiB short" when
  it is too big), so a stretch never shows a negative number. A key under the machine's memory explains them, and
  the Tested column is left out when every row has the same label, which the key then names. The model
  lists say yes, stretch or too big in their Fits column, and a model that fits no setting shows its smallest
  one. Nothing is greyed out: a stretch asks first, and a model or context that needs more memory than the
  machine has gets a stronger warning and can still be downloaded at your own risk. The summary repeats it.
- Vision projectors are recognised whatever their name (`Model.mmproj-Q8_0.gguf`, `Model-mmproj-F16.gguf`,
  `Model.BF16-mmproj.gguf`), not only when the name starts with "mmproj". Before, such a file counted as a model;
  the catalog often read a repo's header from it and then hid the whole repo, and vision was missing. The shipped
  catalog lists 45 more repos (2,590 usable files instead of 2,222), among them mradermacher's 9B, 35B-A3B and
  122B, prithivMLmods' 122B MTP and NVFP4 builds and vultr's 9B, 35B-A3B and 397B, and 45 more repos have vision.
  Headers are no longer read from projectors or MTP heads, a catalog refresh re-reads any that were, and the
  quick search for models on disk no longer lists such a projector as a model.
- For small machines: a 32k context option. 32k is only suggested first when no longer context fits, since coding
  agents' own instructions fill much of it, and the context screen says so.
- A "best for small memory" sort that weighs everything at once: memory left (30%), quant quality by bits per
  weight (25%), model size (15%), context (10%), popularity from downloads and likes (10%) and trust from source,
  testing and version (10%). Each model shows its highest-scoring setting, and each row's note gives the score and
  its parts, labelled as a heuristic, not measured.
- In the full screen, typed answers (a model path, search, port, folder, "I understand") and yes/no questions are
  read key by key, so Esc acts at once instead of printing ^[ and waiting for Enter: it goes back where there is
  a step to go back to, keeps the old value for a search or the port, and answers no to a yes/no question.
  Backspace deletes, Ctrl+U clears the line, and a long answer scrolls in its line. Menus of letters only (such
  as the first menu when no models were found) take one key press without Enter. Non-ASCII characters in a path
  are read whole. `launch` and `remove` use the same prompter as setup.
- Esc on the models folder screen goes back to the settings, and Esc on the summary goes back to the folder
  screen, keeping every choice. Before, the only way out after the settings was "Go ahead? No", which ended
  setup.
- Quitting (q, or Esc where a menu has no Back) clears the full screen first, so only the closing message is
  left, such as what was kept. Every quit goes through one function; a quit no command handled ends with
  "Stopped. Nothing was changed." instead of an error. Plain menus (pipes, SSH, `--plain`) are not cleared.
- Lists redraw at the new size soon after the window is resized, and list rows are cut at the terminal's edge
  in the plain screens too, so long rows never wrap and pull the columns apart. Columns are measured in terminal
  columns, so wide (CJK) characters, tabs or newlines in a name cannot push a column out of line, and the notes
  under a plain list stand apart from the table.
- `scan --full` uses the setup lists' wording in its Fits column (yes, stretch or too big, with a key under the
  table) instead of yes or no, and a suggestion that only fits once other apps close says so.
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
