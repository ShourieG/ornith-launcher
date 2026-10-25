# Troubleshooting

Start with `./ornith-launcher doctor`: it checks everything the launcher needs and prints the fix for each problem
it finds. The sections below cover what it cannot fix for you.

## Docker

| Message | Fix |
|---|---|
| Docker is not installed | Install Docker Engine: https://docs.docker.com/engine/install/ |
| Docker is installed, but this user may not use it (permission denied) | `sudo usermod -aG docker $USER`, then log out and back in |
| Docker is installed but not running | `sudo systemctl start docker` |
| Docker cannot use the NVIDIA GPU | Install the NVIDIA Container Toolkit, then `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker` |
| Docker is installed as a snap | The snap can only read files under your home folder: keep models there, or install Docker Engine from apt |

The launcher never runs `sudo` itself; it prints the command for you.

## Launching

- "The model file ... is missing" or "changed since setup": run `./ornith-launcher setup --repair NAME`. A model
  that belongs to another app (Ollama, LM Studio, the Hugging Face cache) may have been removed or updated there.
- "Another Ornith server is running": two large models rarely fit in memory together. Stop the other one with
  `./ornith-launcher stop`.
- "The server stopped while loading": the last log lines are printed; the full log is `docker logs
  ornith-launcher-NAME`. Running out of memory is the usual cause. Pick a smaller context or quant with
  `./ornith-launcher setup`.
- SELinux hosts (Fedora, RHEL) block bind mounts; the launcher runs its container with `label=disable` and never
  relabels your files.

## Claude Code

- Use `ornith-claude` (or `ornith-claude-NAME`), not `claude`. Plain `claude` keeps using your Anthropic account.
- "The Ornith server is not running": start it with `./ornith-launcher launch NAME`.
- Requests fail with HTTP 500: the model's chat template rejects a system message in the middle of a
  conversation. Connect Claude Code through `./ornith-launcher setup --integrations NAME` so the template fix is
  applied, then restart the server.
- Long prompts time out: the wrapper raises `API_TIMEOUT_MS` for long contexts. Set it higher yourself if needed,
  for example `API_TIMEOUT_MS=7200000 ornith-claude`.
- `~/.local/bin` is not on your PATH: run the wrapper by its full path, or add the folder to PATH.

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

Ctrl+C pauses a download; run `./ornith-launcher setup` again to resume where it stopped. A file that fails its
sha256 check is removed and downloaded again on the next run.
