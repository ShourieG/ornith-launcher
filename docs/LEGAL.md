# Licences, responsibilities and trademarks

This page is not legal advice. It records what ornith-launcher distributes, which licences apply to what it
downloads, and the checks the owner completes before the repository goes public.

## What the project distributes

| Item | Distributed by this project? | Licence |
|---|---|---|
| Our code, docs, benchmark data, the catalog of Hugging Face metadata | Yes | Apache-2.0 |
| Model weights (GGUF files) | No. Users download them from Hugging Face | Each model's own licence |
| Chat templates | No copies. The project ships transformation rules and hashes | Not applicable (the rules are our code) |
| llama.cpp | No. Users pull the Docker image from ghcr.io | MIT (ggml.org) |
| Claude Code, OpenCode, Docker, NVIDIA Container Toolkit | No. The launcher links to or runs their official installers, with consent | Their own terms |
| Benchmark test text | No. Built on the user's machine from `/usr/share/doc` | Mixed, not ours |

The catalog (`src/ornith_launcher/data/catalog-seed.json`) holds metadata only: repository ids, file names,
sizes, sha256 checksums, licence tags and the architecture numbers from GGUF headers. It holds no weights and no
model card text.

## Licences of what users download

- Ornith 1.0 and 1.5 (every official repository) declare MIT in their metadata and model cards. The LICENSE file
  the cards link to is missing in every repository (HTTP 404, checked on 2026-10-12, 2026-10-18 and again on
  2026-10-25 for the 1.5 122B). The project has asked, or will ask, Ornith AI to publish it.
- The Ornith cards name Qwen3.5 (Apache-2.0) and Gemma 4 as the base models. The Gemma 4 licence has not yet been
  checked against Google's own terms; see the checklist below.
- Community GGUF builds each declare their own licence (MIT, Apache-2.0, "other" or none). The launcher shows it
  on every row and asks for an explicit confirmation when a model declares "other" or nothing.
- llama.cpp: MIT.
- Hugging Face's terms keep content under its licence and do not allow removing references to that licence, so
  the launcher always displays licences instead of hiding them.

## How the launcher handles responsibility

- First-run disclaimer, versioned; it is shown again when the text changes.
- Licence column on every model row; "other" or missing licences need "I have checked the licence".
- Community builds are marked "third-party build, not from Ornith AI".
- Uncensored models are hidden until the user opts in, and choosing one needs the typed words "I understand".
  `--yes` never gives that answer; it needs `--accept-uncensored`.
- Gated models are shown as needing a login; the launcher never asks for tokens.
- The launcher asks before installing Claude Code or OpenCode, editing their configuration, creating shortcuts,
  downloading (with the size shown) and deleting anything. It backs up any config file before changing it.

## Trademarks

- No Anthropic, Claude, OpenCode or Ornith logos anywhere, and no "Claude" in the project name.
- Third-party names appear only to describe compatibility, for example "creates a profile for Claude Code".
- The name ornith-launcher contains "Ornith", so it is always paired with "unofficial". If Ornith AI objects to
  the name, the project will be renamed.

## Checklist before going public (the owner ticks each item)

- [ ] `LICENSE` is the unmodified Apache-2.0 text, and GitHub detects it as Apache-2.0.
- [ ] `NOTICE` names "Copyright 2026 Shourie Ganguly", says the project is unofficial and not affiliated with the
      companies named, and has the trademark sentence.
- [ ] The README's first line and the repository description say "Unofficial".
- [ ] `DISCLAIMER.md` covers every point in the design's section 13.3, and the app shows it on first run.
- [ ] No model weights, chat-template copies, test corpus or other third-party text is in the repository or its
      history. `ideate/` was never committed: `git log --all -- ideate` prints nothing.
- [ ] No logos of Ornith AI, Anthropic, OpenCode or others anywhere (README, icons, shortcuts).
- [ ] `CREDITS.md` names everyone the project builds on, with links and licences.
- [ ] The Gemma 4 licence claim was checked against Google's own page, or reworded.
- [ ] Uncensored models need the typed confirmation, and `--yes` does not bypass it.
- [ ] The app asks before installing anything or editing configs, and backs up edited files.
- [ ] The Anthropic "not supported" sentence is quoted in the disclaimer and on the Claude Code option.
- [ ] Every benchmark number carries TESTED, INFERRED or ESTIMATED.
- [ ] The post to Ornith AI has been made, and any reply has been handled.
- [ ] The macOS and Windows CI jobs are re-enabled in `.github/workflows/ci.yml` and green.
