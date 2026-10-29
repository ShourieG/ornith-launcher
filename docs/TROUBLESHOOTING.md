# Troubleshooting

Start with `./ornith-launcher doctor`: it checks everything the launcher needs and prints the fix for each problem
it finds. The sections below cover what it cannot fix for you.

## Python

- "needs Python 3.9 or newer; python3 here is 3.8": your distribution's `python3` is too old (Ubuntu 20.04 has
  3.8, RHEL 8 has 3.6). Install a newer one alongside it, for example `sudo apt install python3.9` or `sudo dnf
  install python39`; `./ornith-launcher` finds `python3.9` to `python3.14` by name.

## Docker (Linux)

| Message | Fix |
|---|---|
| Docker is not installed | Install Docker Engine: https://docs.docker.com/engine/install/ |
| Docker is installed, but this user may not use it (permission denied) | `sudo usermod -aG docker $USER`, then log out and back in |
| Docker is installed but not running | `sudo systemctl start docker` |
| Docker cannot use the NVIDIA GPU | Install the NVIDIA Container Toolkit, then `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker` |
| Docker is installed as a snap | The snap can only read files under your home folder: keep models there, or install Docker Engine from apt |

Podman (including its `docker` alias) and rootless Docker are untested. Reports are welcome.

The launcher never runs `sudo` itself; it prints the command for you.

## Launching

- "The model file ... is missing" or "changed since setup": run `./ornith-launcher setup --repair NAME`. A model
  that belongs to another app (Ollama, LM Studio, the Hugging Face cache) may have been removed or updated there.
- "Another Ornith server is running": two large models rarely fit in memory together. Stop the other one with
  `./ornith-launcher stop`.
- "The server stopped while loading": the last log lines are printed; the full log is `docker logs
  ornith-launcher-NAME` on Linux, or `~/.local/state/ornith-launcher/logs/NAME.log` on macOS. Running out of
  memory is the usual cause. Pick a smaller context or quant with `./ornith-launcher setup`.
- "The model server (llama.cpp image) is not on this machine": `launch` offers to download it. If you say no,
  run `./ornith-launcher setup --repair NAME` later.
- The desktop icon asks "Allow Launching" (GNOME without `gio`, or other desktops): right-click it and choose that
  once.
- The server's own log shows in the server window, between the status lines (they start with `--`). Closed that
  window? `./ornith-launcher logs NAME` shows the same log in any terminal; Docker (or the log file on macOS)
  keeps it, so nothing is lost.
- Closed the chat log window by mistake: press `c` and Enter in the server window, or run
  `./ornith-launcher chat-log NAME` in any terminal. The new window shows the last 10 requests again. Ctrl+C in
  the chat log window never stops the server.
- No chat log window opens: setup only offers it on a desktop with a terminal app. Over SSH, run
  `./ornith-launcher chat-log NAME` in a second session.
- "port N is in use by another program, so the chat log cannot listen there": another program has the profile's
  port. Close it, or start without the chat log this time with `./ornith-launcher launch NAME --no-chat-log`.
- "already running with different settings (the chat log is on for one and off for the other)": the server runs
  without the chat log and this launch wants it, or the other way round. Stop it with
  `./ornith-launcher stop NAME`, then launch again.
- The chat log shows chat requests only (`/v1/chat/completions`, `/v1/completions`, `/completion` and
  `/v1/messages`). Other requests pass through without showing.
- SELinux hosts (Fedora, RHEL) block bind mounts; the launcher runs its container with `label=disable` and never
  relabels your files.

## macOS

- "macOS ... is older than 13.3, which the pinned llama.cpp build needs": update macOS in System Settings >
  General > Software Update. Setup cannot continue on an older version.
- "This is an Intel Mac": the model runs on the CPU only, which is much slower. Pick a small model, such as a 9B
  at Q4_K_M with a short context.
- The server does not start and `./ornith-launcher doctor` says macOS marked the files as downloaded from the
  internet: run the `xattr -dr` command doctor prints. The files' sha256 was checked when they were downloaded.
- Finder asks before opening `Ornith NAME.command` the first time: choose Open. It does not ask again.
- `ornith-claude` is not found after setup: `~/.local/bin` is not on your PATH. Setup prints the line to add to
  `~/.zprofile`; open a new Terminal window after adding it.
- A model in `~/Downloads` is not found: the quick search skips that folder on macOS, because macOS asks before an
  app reads it. Give its path with `p` in setup, or `./ornith-launcher setup --model PATH`.
- Setup plans against the GPU memory limit Metal sets, not the whole memory, so a model can be "too big" on a Mac
  that has enough memory in total. The launcher never changes that limit.
- The server window shows no GPU temperature: macOS reports it only to root, so `bench` cannot pause for heat
  either.

## Model lists

- Ctrl+click (Cmd+click on macOS) a model name to open its Hugging Face card. Konsole only follows links outside
  full-screen lists, so the highlighted model's URL is printed under the list for you to Ctrl+click instead.
- Use `--plain` for numbered menus if the full-screen list draws badly in your terminal.
- In the model lists and on the context screen, "stretch" means the setting fits only by using part of the 15% of
  memory the launcher keeps free, or only after other apps close; it can slow down or fail under load. "Too big"
  means it needs more memory than the machine has after the system's own use: it will most likely not load. Both
  can still be chosen after a warning (too big at your own risk, for example to move the model to a bigger
  machine later). Pick a shorter context, or a smaller quant in the list. All memory figures are estimates
  (ESTIMATED or INFERRED in the Tested column).

## Uninstall

- The menu only lists what applies: the setup options once a setup exists, and Remove unfinished downloads while a
  stopped download is left. That option deletes only the stopped downloads (all of them, or the one you pick).
- The model server (llama.cpp image) is only offered when ornith-launcher pulled it and no other setup needs it.
- If the image has other tags, uninstall removes only the launcher's reference, and the image stays for the others.

## Claude Code

- Use `ornith-claude` (or `ornith-claude-NAME`), not `claude`. Plain `claude` keeps using your Anthropic account.
- "The Ornith server is not running": start it with `./ornith-launcher launch NAME`.
- Requests fail with HTTP 500: the model's chat template rejects a system message in the middle of a
  conversation. Connect Claude Code through `./ornith-launcher setup --integrations NAME` so the template fix is
  applied, then restart the server.
- Long prompts time out: the wrapper raises `API_TIMEOUT_MS` for long contexts. Set it higher yourself if needed,
  for example `API_TIMEOUT_MS=7200000 ornith-claude`.
- `~/.local/bin` is not on your PATH: run the wrapper by its full path, or add the folder to PATH.
- In auto mode, a session stops at "We're changing auto mode to no longer charge for classifier requests in
  Claude Code. However, this session isn't eligible...": the wrapper was made before the launcher set
  `CLAUDE_CODE_AUTO_MODE_SERVER=0`. Run `./ornith-launcher setup --integrations NAME` and keep Claude Code to
  rewrite it. Every request goes to your local server, so nothing is billed by Anthropic either way.
- Actions in auto mode wait a long time before they run: each check is a separate request to the local model
  with a long prompt. Switch to Manual mode with Shift+Tab to approve actions yourself.

## OpenCode

- In OpenCode, pick the model with `/models`, then "Ornith (local, ornith-launcher)".
- If your `opencode.json` has comments, the launcher shows the entry to paste instead of rewriting the file.

## Models found on the machine

- The quick search reads only the usual folders (the Hugging Face cache, LM Studio, Ollama, the llama.cpp cache,
  `~/models`, `~/Downloads`, the models folder and folders you entered). Use `./ornith-launcher setup --model
  PATH` for anything else.
- A split model with missing parts is shown as incomplete and cannot be used.
- The launcher never moves, copies or deletes a model it found; `remove` and `uninstall` leave it where it is.

## Downloads

Ctrl+C pauses a download and asks what next: Resume later keeps what has arrived (the default, also for a second
Ctrl+C), and Delete it and exit removes it. To resume, run `./ornith-launcher setup`: its first menu says a download
was stopped, and `r` (Resume a download) lists it with how much is done and carries on where it stopped, in the same
folder and with the settings you chose before. If those settings no longer fit the machine, setup asks for them
again. To delete it later, choose `u` (Uninstall), then Remove unfinished downloads. A file that fails its sha256
check is removed and downloaded again on the next run.
