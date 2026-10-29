# ornith-launcher guide

The full reference for ornith-launcher: every screen, key, option and file. The [README](../README.md) covers what
the tool is and how to get started.

- [New to local models?](#new-to-local-models)
- [Setup, step by step](#setup-step-by-step)
- [Choosing a model yourself](#choosing-a-model-yourself)
- [Choosing a size, a quant and a context](#choosing-a-size-a-quant-and-a-context)
- [Settings](#settings)
- [Running a model](#running-a-model)
- [Using the model from other tools](#using-the-model-from-other-tools)
- [Uninstalling](#uninstalling)
- [Commands and options](#commands-and-options)
- [Files and settings](#files-and-settings)
- [Linux](#linux)
- [macOS](#macos)

## New to local models?

You can run setup without knowing any of this; every choice has a recommended default. These are the words you
will see along the way:

| Word | What it means |
|---|---|
| llama.cpp | The open-source program that loads the model and answers requests. The launcher fetches a pinned build of it and starts it for you |
| GGUF | The model file format llama.cpp reads. Its header says how big the model is, which the launcher uses to work out memory |
| Quant | How finely the model's numbers are stored, such as Q8_0 (8 bits) or Q4_K_M (4 to 5 bits). Fewer bits make a smaller file that needs less memory, at some cost in quality |
| Context | How many tokens (pieces of words) the model can read at once. A longer context needs more memory |
| KV cache | The memory that holds the context while the model works. It grows with the context |
| Prefill | The time the model spends reading your prompt before it starts answering. It grows with the prompt |
| YaRN | A way to stretch a model past its native context. The launcher sets it up for contexts above 262k |
| Vision file | An extra file (`mmproj`) that lets a model read images. Setup can leave it out to save memory |
| Fits, stretch, too big | Whether a choice fits and leaves the launcher's 15% memory reserve alone, fits only by using part of that reserve, or does not fit |

## Setup, step by step

Setup runs as full-screen lists in a terminal, or as numbered menus with `--plain` (also used for pipes and SSH).
Esc goes back a step at any point before the download and keeps your choices. Each question starts on a fresh
screen; what came before stays in the terminal's scrollback.

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
| Finishing | The download, then the coding tools, terminal, chat log, the optional desktop icon and menu entry, and whether to open the coding tools at each launch | Answer each question; Enter takes the default |

Setup saves a profile for every model you set up. The first profile becomes the default, so commands without a
name use it. If you then answer no to "Launch now?", setup clears the screen and shows how to start the model.

The highlighted row in a list shows its file, the reason for its verdict, its badges and a link to its model card.
The badges mean:

| Badge | Meaning |
|---|---|
| TESTED | Measured on real hardware, with the results in [BENCHMARKS.md](BENCHMARKS.md) |
| INFERRED | Worked out from measured runs of the same architecture |
| ESTIMATED | Calculated from the GGUF header and the memory model only; never run |
| UNTESTED LOCALLY | A size the project has never run on any machine (the 397B) |
| LOW-BIT | A 1- or 2-bit quant (IQ1, IQ2, Q1, Q2, TQ1, TQ2); expect lower quality |
| VERSION UNKNOWN | The Ornith version could not be told from the repo, the file or its lineage |
| UNCENSORED | Labelled as a model with its safety behaviour removed (see below) |
| NO LICENCE, OTHER LICENCE | The repo declares no licence, or a custom one; read the model card |

On a Mac, the highlighted row also carries a note (ESTIMATED): I-quants usually generate a little slower on Apple
GPUs than a K-quant of similar size, and a BF16 vision file may be slower on M1.

### Models already on this machine

The first menu lists Ornith models found in the Hugging Face cache, LM Studio, Ollama, the llama.cpp cache,
`~/models` and, except on macOS, `~/Downloads`. `p` takes any other path. A model found this way is used where it
is and never moved, copied or deleted.

### Downloads

Downloads come from Hugging Face without a login, pinned to a revision and checked against their sha256. A server
address that does not answer within 10 seconds is skipped for the next one, and tried last for the rest of the
download. Ctrl+C asks whether to keep or delete what has arrived; a kept download shows up on the first menu next
time, and `r` resumes it with the settings you already chose.

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
model. See [DISCLAIMER.md](../DISCLAIMER.md).

## Choosing a size, a quant and a context

Setup's suggestions already weigh these trade-offs. This section explains them, for when you want to pick
yourself. Apart from the results marked TESTED, which come from this project's own runs, the points below come from
published studies of other models (mostly dense Llama-style models) and from llama.cpp's own measurements. None of
them has been measured on Ornith, so read them as general guidance, not as Ornith results.

### Strategies

| Situation | Strategy |
|---|---|
| A bigger model fits only at Q4_K_M or better | Take the bigger model: about 4 bits is usually the best use of memory (1, 2) |
| A bigger model fits only at 2 or 3 bits (IQ1, IQ2, Q2, Q3) | Take a smaller model at Q4_K_M or better instead: quality drops fast below 4 bits (2, 3) |
| Memory is left over at Q6_K | Spend it on context or parallel requests rather than Q8_0: Q6_K is within 0.1% of the unquantized model (2) |
| Choosing a quant for the 9B | Lean towards Q5_K_M or Q6_K: small models trained on a lot of text lose more to quantization (4) |
| Using a coding agent | Keep 64k of context or more, and drop a quant step before dropping below 64k: the agent's own instructions fill much of a short context |
| Reading long documents | Use the native 262k first; 524k and 1M (YaRN) only when a prompt really needs them, since they cost memory and prefill time (6, 7) |

### What the evidence says

- **About 4 bits per weight is the sweet spot.** Testing many model families, Dettmers and Zettlemoyer found that
  "4-bit precision is almost universally optimal for total model bits and zero-shot accuracy" (1). For the same
  memory, a bigger model at about 4 bits usually beats a smaller one at 8 or 16 bits.
- **Above 4 bits there is little to gain; below 3 bits a lot to lose.** In llama.cpp's k-quants work, a 7B
  LLaMA's perplexity rose over the unquantized model by about 0.07% at Q6_K, 0.2% at Q5_K_M, 0.9% at Q4_K_M, 4% at
  Q3_K_M and 15% at Q2_K (2). A study of Llama 3 found "non-negligible degradation", particularly at ultra-low
  bit widths (3).
  Setup marks 1- and 2-bit files LOW-BIT for this reason.
- **Heavily trained models are more sensitive.** Ouyang and others found that "low-bit quantization favors
  undertrained" models: larger models, or models trained on fewer tokens, lose less, and small models trained on
  very many tokens lose the most (4). Small modern models are usually trained on a great deal of text, which
  suggests that low quants deserve more caution on the 9B than on the bigger sizes.
- **Mixture-of-Experts models are different.** The 35B-A3B reads only about 3B of its weights for each token, so it
  generates faster than its size suggests, but all 35B must still fit in memory. A benchmark of MoE quantization
  found that one precision for every part is not the best choice, and that experts every token uses "potentially
  need higher precision" (5). Setup prefers the 35B over the 9B only from Q6_K up; that threshold is this
  project's own cautious choice and has not been measured.
- **A longer context is not a better one by default.** RULER found that of 17 models that all claim 32k or more,
  "only half of them can maintain satisfactory performance at the length of 32K", and almost all lose accuracy as
  the context grows (6). Models also use information at the start or end of a long prompt best, and the middle
  least (8), so put what matters most at either end.
- **What this project measured.** On Ornith 1.5 35B-A3B Q8_0, YaRN x2 made no measurable difference to short-context
  perplexity and x4 cost at most 0.4%, and an easy retrieval test found every code up to 917k tokens (TESTED; see
  [BENCHMARKS.md](BENCHMARKS.md)). That test says nothing about reasoning over a long context, which (6) and (8)
  suggest gets harder with length.
- **KV cache.** A q8_0 KV cache needs about half the memory of f16, but it has not been tested with Ornith, so
  setup keeps f16 and labels q8_0 UNTESTED.

### Sources

1. Tim Dettmers and Luke Zettlemoyer, [The case for 4-bit precision: k-bit Inference Scaling
   Laws](https://arxiv.org/abs/2212.09720), arXiv:2212.09720, 2022.
2. Iwan Kawrakow, [k-quants](https://github.com/ggml-org/llama.cpp/pull/1684), llama.cpp pull request 1684, 2023
   (perplexity table for a 7B model).
3. Wei Huang and others, [An empirical study of LLaMA3 quantization: from LLMs to
   MLLMs](https://arxiv.org/abs/2404.14047), arXiv:2404.14047, 2024.
4. Xu Ouyang, Tao Ge, Thomas Hartvigsen, Zhisong Zhang, Haitao Mi and Dong Yu, [Low-Bit Quantization Favors
   Undertrained LLMs](https://arxiv.org/abs/2411.17691), arXiv:2411.17691, 2024.
5. Pingzhi Li, Xiaolong Jin, Zhen Tan, Yu Cheng and Tianlong Chen, [QuantMoE-Bench: Examining Post-Training
   Quantization for Mixture-of-Experts](https://arxiv.org/abs/2406.08155), arXiv:2406.08155, 2024.
6. Cheng-Ping Hsieh and others, [RULER: What's the Real Context Size of Your Long-Context Language
   Models?](https://arxiv.org/abs/2404.06654), COLM 2024.
7. Bowen Peng, Jeffrey Quesnelle, Honglu Fan and Enrico Shippole, [YaRN: Efficient Context Window Extension of
   Large Language Models](https://arxiv.org/abs/2309.00071), arXiv:2309.00071, 2023.
8. Nelson F. Liu and others, [Lost in the Middle: How Language Models Use Long
   Contexts](https://arxiv.org/abs/2307.03172), TACL, 2023.

## Settings

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

`./ornith-launcher launch [NAME]`, the desktop icon and `l` on setup's first menu all start a profile's server in
its own window, with the server's log and a status line every 15 seconds. Ctrl+C in that window asks "Stop the
server and free its memory?" (default yes). Closing the window leaves the server running. If another model is
already running, the launcher asks whether to stop it first (default yes), because two large models rarely fit in
memory together.

- `./ornith-launcher status` shows what is running, how much memory it uses and where the models are stored;
  `--command` adds the full llama.cpp command.
- `./ornith-launcher monitor` watches a server that is already running.
- `./ornith-launcher logs` follows its log.
- `./ornith-launcher stop` stops it.

### Chat log

Setup can give a model a chat log. Every launch then opens a second window that shows each request sent to the
model and its reply as it streams: thinking, answer, tool calls, tokens and speed. It is useful for seeing what a
coding tool sends to the model.

A small proxy on the model's port passes everything through, so clients keep the same URL. It keeps the last 10
requests in memory and writes nothing to disk. Closing the chat log window closes only that window; press `c` and
Enter in the server window to open it again. A desktop icon or menu entry on Linux also gets a "Show chat log"
action, and `launch --chat-log` or `--no-chat-log` changes the setting for one launch.

## Using the model from other tools

The server listens on `127.0.0.1` at the profile's port and speaks the OpenAI-compatible API that llama.cpp
provides, so most tools that take a custom OpenAI base URL can use it. With the default port:

```
curl http://127.0.0.1:18400/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello"}]}'
```

`./ornith-launcher status` shows the port each profile uses.

### Installing Claude Code and OpenCode

For a tool you connect that is missing, setup names its command (`claude` or `opencode`), shows the official
installer command, and runs it only after a yes. The installer's own output stays on screen; while it prints
nothing, for example while Claude Code's installer downloads, a spinner shows the time so far. Setup records each
tool it installs, so uninstall can remove it later; a tool you installed yourself is never offered for removal.

Both are command-line apps. Afterwards setup says to type the command in a new terminal window, and checks that a
new window would find it: it starts your shell the way a terminal window does, from a clean environment. If the
command would not be found, it prints the exact line that adds its folder to your PATH; it never edits your shell
files itself. OpenCode's installer writes its own PATH line, but only when the folder is not on PATH already, so
setup runs it without that folder. Otherwise a terminal opened before an earlier uninstall would stop the line
from being written.

### Claude Code

Run `ornith-claude` (the default profile) or `ornith-claude-NAME` instead of `claude`. Plain `claude` still uses
your Anthropic account, and your own Claude Code settings are not changed. Setup applies a small fix to a copy of
the model's chat template so Claude Code's mid-conversation system messages work; the model file is not changed.
If `~/.local/bin` is not on your PATH, setup prints the line to add.

Anthropic does not support Claude Code with non-Claude models; see [DISCLAIMER.md](../DISCLAIMER.md).

In auto mode, Claude Code checks each action it would otherwise ask you about by sending a separate request to the
model, so through `ornith-claude` the local Ornith model makes those checks. Each check carries more than 140,000
characters of Claude Code's own instructions (TESTED with Claude Code 2.1.312), so it adds a prefill wait before
the action runs. Claude Code's checks are written for Claude models, and how well an Ornith model judges them has
not been tested. Manual mode (Shift+Tab) asks you instead. The wrapper sets `CLAUDE_CODE_AUTO_MODE_SERVER=0`,
because llama.cpp cannot run Anthropic's server-side checks; without it, Claude Code stops each auto mode session
at a notice about classifier charges.

### OpenCode

Setup adds the model to your OpenCode config after backing the file up, and it appears under `/models` as
"Ornith (local, ornith-launcher)". With vision on, the entry also tells OpenCode that the model reads images;
without that, OpenCode refuses to send it any. Setup asks whether to make the model OpenCode's default (default
yes), so new sessions start with it, in OpenCode's desktop app too. If the config has comments, which a rewrite
would lose, setup shows the entry for you to paste instead and asks before rewriting the file.

Uninstall removes only the entry setup added, and never entries you edited yourself. It puts back the default
model you had before setup, unless you picked another one since.

Ornith models think before they answer, and OpenCode shows the thinking. They have no effort levels, so instead of
low, medium and high the entry offers one variant, "no-thinking", for quick answers: pick it with OpenCode's
variant switch or `opencode run --variant no-thinking`. Without a variant the model thinks.

OpenCode's chat (the desktop app and `opencode web`) shows a picture when it is part of the model's reply, written
as an image with a `data:` address, but not an image a tool read and not a file on disk (checked in OpenCode 1.19).
So setup also adds a short file, `show-pictures.md` in the launcher's data folder, to OpenCode's `instructions`.
OpenCode puts the files listed there into the system prompt of every session, for every model you use in
OpenCode, and setup says so before you pick OpenCode and again when it adds the file. The file tells the model to
draw charts and diagrams as SVG inside its reply. A model that can run commands writes the SVG to a file, and a
short script checks it, encodes only `%` and `#` and prints the whole tag for the model to paste; otherwise it
writes the tag by hand from a complete example and strict rules. Uninstall lists the file and takes it out again
with the last Ornith model. With vision on, the model also sees the images you attach and the images its tools
read.

### Opening the tools at each launch

The last setup question is whether each launch should also open the connected tools, each in its own window and
already pointed at the model: "Open Claude Code when this model starts?", or with both connected a choice of
Neither, Claude Code, OpenCode or Both. The default is yes (Both when both are connected), then a folder for them
to open in (your home folder by default). They open once the server answers, in the same terminal app as the
server window, and stay open when the server stops, since they may hold your work. `launch --no-tools` skips them
once, and `setup --integrations NAME` asks again.

## Uninstalling

`./ornith-launcher uninstall` (or `u` on setup's first menu) offers only what applies:

- Remove one setup. You then choose what goes:

  | Choice | Removes |
  |---|---|
  | Setup only (the default) | The profile, its server log (macOS), its launch icon and menu entry, its OpenCode entry and its Claude Code wrapper and alias, where they exist. The model stays |
  | Setup and its model | The same, plus the model it downloaded. A model found elsewhere is never deleted, so this is not offered for one |
  | The model server | The llama.cpp image on Linux, or the llama.cpp runtime folder on macOS. The setup stays |
  | Setup, model and model server | All three |

- Remove all setups. You then choose what goes:

  | Choice | Removes |
  |---|---|
  | Setups only (the default) | Every profile with its launch icons and coding-tool entries. The models stay |
  | Setups and their models | The same, plus the models they downloaded |
  | Setups, models and llama.cpp | The same, plus the model server setup fetched. Offered only when there is one |
  | Everything | Also every other model it downloaded, unfinished downloads and the launcher's own settings |

- Remove unfinished downloads deletes the downloads you stopped and keeps finished models.
- Remove coding tools appears when setup installed Claude Code or OpenCode, and removes the ones you pick the way
  each tool documents: `opencode uninstall` (which also takes out the PATH line its installer added), then the
  rest of `~/.opencode`, which its uninstaller leaves; or deleting `~/.local/bin/claude` and
  `~/.local/share/claude`. Their own settings and history stay unless you answer yes to "Also delete … own
  settings and history?" (default no): `~/.config/opencode` and `~/.local/share/opencode`, or `~/.claude` and
  `~/.claude.json`.
- Remove everything appears on its own once no setup is left. Like Everything above, it asks whether to remove the
  coding tools setup installed too (default no).

A screen lists everything that will be deleted, and what is kept, before anything goes. Uninstall removes only
what the launcher's own records list. Models it found elsewhere are never touched, and the model server is not
offered if it was there before setup or another setup still uses it. On macOS, if App Management stops the
terminal from deleting a launch icon app, Finder moves it to the Trash instead.

`./ornith-launcher remove NAME` removes a single profile and asks before deleting downloaded files.

## Commands and options

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
| `ornith-launcher doctor` | Check Docker or the macOS runtime, the GPU, disk, profiles and tools (read-only) |
| `ornith-launcher remove NAME` | Remove a profile; asks before deleting downloaded files |
| `ornith-launcher uninstall` | Remove setups, models, the model server, coding tools it installed, or everything it created |
| `ornith-launcher bench [NAME]` | Long-context retrieval and perplexity tests (long, hot GPU runs) |

`NAME` is a profile name; without it, commands use the default profile. Every command takes `-h` for its help.
Run from a clone, the command is `./ornith-launcher`.

Global options go before the command:

| Option | What it does |
|---|---|
| `--version` | Print the version |
| `--verbose` | Log details to stderr |

`setup`:

| Option | What it does |
|---|---|
| `--model PATH` | Use this model file or folder and skip the search |
| `--models-dir PATH` | Folder for downloaded models (default `~/models/ornith-launcher`) |
| `--port PORT` | Port for the server (default 18400, or the next free one) |
| `--name NAME` | Profile name (default: made from the model and context) |
| `--plain` | Numbered menus instead of full-screen lists |
| `--yes` | Accept every default without asking |
| `--accept-uncensored` | Accept an uncensored model with `--yes`; `--yes` alone never does |
| `--repair NAME` | Put back what a profile needs: the model server, a missing download, a changed model file |
| `--integrations NAME` | Connect an existing profile to Claude Code or OpenCode |
| `--shortcuts NAME` | Add a desktop icon or applications menu entry for an existing profile |

`launch`:

| Option | What it does |
|---|---|
| `--here` | Run in this terminal instead of opening a window |
| `--window` | Always open a terminal window and return (what the launch icon uses) |
| `--no-monitor` | Start the server and return |
| `--chat-log` | Open the chat log window this time |
| `--no-chat-log` | No chat log this time |
| `--no-tools` | Do not open the coding tools this time |

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
| `status` | `--command` | Also show the command that starts the server |
| `chat-log` | `--window` | Open it in a new window, closing one that is still open |
| `remove` | `--yes` | Accept the defaults |
| `bench` | `--needle` | Long-context retrieval test |
| `bench` | `--perplexity` | Short-context quality test |
| `bench` | `--lengths N,N,...` | Prompt lengths in tokens for `--needle` (default 120000,250000,480000,950000) |

`bench` runs the GPU hot for a long time and, on Linux, pauses when it gets too hot. Results so far are in
[BENCHMARKS.md](BENCHMARKS.md).

## Files and settings

| What | Where |
|---|---|
| Settings and profiles | `~/.config/ornith-launcher` (or `$XDG_CONFIG_HOME/ornith-launcher`) |
| Model catalog cache | `~/.cache/ornith-launcher` |
| Downloaded models | `~/models/ornith-launcher`, or the folder you pick |
| Claude Code wrappers | `~/.local/bin/ornith-claude` and `~/.local/bin/ornith-claude-<profile>` |
| Desktop icon and menu entry | See [Linux](#linux) and [macOS](#macos) |

| Environment variable | What it does |
|---|---|
| `HF_TOKEN` | Sent to huggingface.co for downloads, if you set it; the launcher never asks for one |
| `HF_ENDPOINT` | Use a Hugging Face mirror |
| `HF_HOME`, `HF_HUB_CACHE` | Where the quick search looks for the Hugging Face cache |
| `LLAMA_CACHE`, `OLLAMA_MODELS` | Where it looks for llama.cpp and Ollama models |
| `XDG_CONFIG_HOME`, `XDG_DATA_HOME` | Where settings and menu entries go |

## Linux

Linux runs llama.cpp in Docker. It has been tested on a DGX Spark with Ubuntu 24.04; other distributions and GPUs
are listed in [HARDWARE.md](HARDWARE.md).

### Requirements on Linux

- Docker Engine. Docker Desktop runs containers in a VM with its own memory limit, and `doctor` warns when that
  limit is below the machine's memory.
- For an NVIDIA GPU, the driver and the NVIDIA Container Toolkit. `doctor` prints the commands to install and
  configure it.

### The server on Linux

Each profile runs in its own container, `ornith-launcher-NAME`, from a llama.cpp image pinned by digest and
published on `127.0.0.1` only. `docker logs ornith-launcher-NAME` shows the same log as `./ornith-launcher logs`.

### One-click launch on Linux

The icon and the menu entry are `.desktop` files named "Ornith: NAME": one in your desktop folder (from
`~/.config/user-dirs.dirs`, else `~/Desktop`) and one in `~/.local/share/applications`. On GNOME the launcher marks
its own desktop icon as trusted, so it starts on the first double-click. Other desktops may ask you to allow
launching once. If you turned on the chat log, right-clicking the icon or menu entry also offers "Show chat log",
on desktops that show actions.

The terminals it can open are the system default and GNOME Terminal, Konsole, Ptyxis, GNOME Console, kitty,
Alacritty, WezTerm, foot, Xfce Terminal, Tilix, Terminator, MATE Terminal, LXTerminal and xterm, when installed.

## macOS

macOS runs the llama.cpp release build for the Mac as a normal process, without Docker. It has been tested on a
MacBook Pro with an Apple M1 Max, 32 GB and macOS 26.6.2; Intel Macs have not been run yet.

### Requirements on macOS

- macOS 13.3 or newer, which the pinned llama.cpp build needs. Setup stops with that message on older versions.
- An Apple Silicon Mac, where the model runs on the GPU through Metal. An Intel Mac runs it on the CPU only, which
  is much slower, so only small models are practical there.
- Python 3.9 or newer: `xcode-select --install` provides 3.9, or install a newer one with `brew install python`.
  The launcher finds `python3.12` and the like next to an older `python3`.

### The server on macOS

The first setup downloads llama.cpp b11552 for macOS (about 12 MB) from the llama.cpp releases on GitHub, shows
the address and its progress, checks it against its pinned sha256 and unpacks the whole folder into
`~/.local/share/ornith-launcher/runtime`. Each profile's server then runs from there on `127.0.0.1` only, in its
own session, so Ctrl+C in the server window asks before stopping it and closing the window leaves it running. Its
log is in `~/.local/state/ornith-launcher/logs/NAME.log`. While it runs, `caffeinate` keeps the Mac from sleeping
when idle, so a long prompt is not cut off; closing the lid still puts it to sleep.

The GPU on a Mac may use only part of the memory, a limit Metal sets. Setup plans with the whole memory less what
the system uses, capped at that limit, and keeps the usual 15% free. It reads the limit from Metal itself; if that
fails, from llama.cpp once the runtime is installed; and only then estimates it (two thirds of the memory up to
36 GiB, three quarters above, ESTIMATED). On the tested M1 Max with 32 GB, Metal reported 24.96 GiB, 78% of the
memory (TESTED reading). The launcher never changes the limit.

Files the launcher downloads itself are not marked as coming from the internet, so Gatekeeper should not block
them. If `./ornith-launcher doctor` finds that mark on the runtime, it prints the `xattr` command that clears it.

macOS reports no GPU temperature without root, so the server window shows memory and swap but no temperature,
and `bench` cannot pause for heat.

### One-click launch on macOS

The icon and the menu entry are small apps named "Ornith NAME": one on your Desktop and one in `~/Applications`,
where Finder, Launchpad and Spotlight find it, and you can keep it in the Dock. Setup builds them on your Mac with
macOS's own `osacompile`, so Gatekeeper does not stop them. A double-click opens the server straight in the
terminal you picked, with no extra Terminal window; for a profile set to "This terminal" it opens Terminal. If
your terminal is iTerm2, macOS asks once whether the app may control iTerm2; choose Allow. If the server cannot
start, the app shows the reason in an alert.

The app keeps the PATH setup ran with, so it finds the same Python. If you install a different Python later and
remove the old one, run `./ornith-launcher setup --shortcuts NAME` again after deleting the app. If `osacompile`
cannot build the app, setup makes an `Ornith NAME.command` file instead, which Finder opens in Terminal; macOS may
ask before opening it the first time, so choose Open. Neither has right-click actions, so reopen the chat log with
c and Enter in the server window, or with `./ornith-launcher chat-log NAME`.

The terminals it can open are Terminal (the default), iTerm2 when installed, and kitty, Alacritty and WezTerm
when they are on your PATH. Terminal is opened with `open -a` and a small script that deletes itself, so macOS
asks nothing. iTerm2 asks "OK to run" before every file it is handed that way, so the launcher types the same
script into a new iTerm2 window through iTerm2's AppleScript instead. The first time, macOS asks whether the app
you started the launcher from (for example Terminal or iTerm2) may control iTerm2; choose Allow. If it was
refused, allow it in System Settings > Privacy & Security > Automation.

### Other differences on macOS

- The quick search for models already on the Mac skips `~/Downloads`, because macOS asks before an app reads
  that folder. Give a path in it with `p`, and macOS asks once.
- Claude Code and OpenCode use the same official installers and config files as on Linux.

| What | Where on macOS |
|---|---|
| llama.cpp runtime | `~/.local/share/ornith-launcher/runtime` |
| Server logs | `~/.local/state/ornith-launcher/logs` |
| Desktop icon and menu entry | `~/Desktop` and `~/Applications` |

To check it on a Mac, with any Ornith GGUF that fits:

```
ORNITH_IT=1 ORNITH_IT_MODEL=/path/to/model.gguf python3 -m unittest tests.integration.test_native -v
```

It installs the runtime into a temporary folder, starts and stops a server, and prints the Metal limit and
llama.cpp's own memory estimate.
