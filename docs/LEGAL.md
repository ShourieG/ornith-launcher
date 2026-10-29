# Licences, responsibilities and trademarks

This page is not legal advice. It records what ornith-launcher distributes, which licences apply to what it
downloads, and how the launcher handles responsibility and trademarks.

## What the project distributes

| Item | Distributed by this project? | Licence |
|---|---|---|
| Our code, docs, benchmark data, the catalog of Hugging Face metadata | Yes | GPL-3.0-or-later |
| Model weights (GGUF files) | No. Users download them from Hugging Face | Each model's own licence |
| Chat templates | No copies. The project ships transformation rules and hashes | Not applicable (the rules are our code) |
| llama.cpp | No. On Linux the user's machine pulls the Docker image from ghcr.io; on macOS it downloads the release build from GitHub | MIT (ggml.org) |
| Claude Code, OpenCode, Docker, NVIDIA Container Toolkit | No. The launcher links to or runs their official installers, with consent | Their own terms |
| Benchmark test text | No. Built on the user's machine from `/usr/share/doc` | Mixed, not ours |

The catalog (`src/ornith_launcher/data/catalog-seed.json`) holds metadata only: repository ids, file names,
sizes, sha256 checksums, licence tags, the architecture numbers from GGUF headers, and each repository's public
like and 30-day download counts. It holds no weights and no model card text; the lists link to each model card on
Hugging Face instead.

## Licences of what users download

- Ornith 1.0 and 1.5 (every official repository) declare MIT in their metadata and model cards. The LICENSE file
  the cards link to is missing in every repository (HTTP 404, checked on 2026-10-12, 2026-10-18, 2026-10-25 for
  the 1.5 122B, and 2026-10-29). The project has asked, or will ask, Ornith AI to publish it.
- The Ornith cards name Qwen3.5 (Apache-2.0) and Gemma 4 as the base models. Google's own Gemma 4 repositories on
  Hugging Face (`google/gemma-4-31B-it`, `google/gemma-4-26B-A4B-it`, `google/gemma-4-E4B-it`) declare
  Apache-2.0 in their metadata (checked on 2026-10-29).
- Community GGUF builds each declare their own licence (MIT, Apache-2.0, "other" or none). The launcher shows it
  on every row and asks for an explicit confirmation when a model declares "other" or nothing.
- llama.cpp: MIT.
- Hugging Face's terms keep content under its licence and do not allow removing references to that licence, so
  the launcher always displays licences instead of hiding them.

## How the launcher handles responsibility

- First-run disclaimer, versioned; it is shown again when the text changes.
- Licence column on every model row; "other" or missing licences need "I have checked the licence".
- Community builds are marked "third-party build, not from Ornith AI".
- Uncensored models are hidden (best effort) until the user opts in, and choosing one needs the typed words
  "I understand". `--yes` never gives that answer; it needs `--accept-uncensored`. The opt-in is saved and
  applies to every list and search, in later runs too, until the user hides them again; the lists say
  "uncensored shown" while it is on.
- "Only uncensored models" (the Uncensored filter in the model lists) needs the same opt-in, shows the uncensored
  warning above the list, and still needs "I understand" before a model is chosen. Its order (best fit, or a sort
  the user picks) is not a recommendation.
- The uncensored label comes from keywords (`data/classification.json`) in names, tags and the models a file was
  made from, so it can be wrong either way. The disclaimer calls it best effort, not a content filter, points
  users to each model card (linked from every list row), and asks them to report unlabelled models.
- Gated models are shown as needing a login; the launcher never asks for tokens.
- The launcher asks before installing Claude Code or OpenCode, editing their configuration, creating shortcuts,
  downloading (with the size shown) and deleting anything. It backs up any config file before changing it.
- Uninstall removes only what the launcher recorded as its own (`installed.json` and the profiles). In other
  tools it removes only its own OpenCode entry, and never edits entries the user changed or the tools' history.

## Responsibility for model content

The launcher cannot guarantee that every uncensored model is labelled: it only sees what uploaders publish. The
project does not try to make anyone else liable for that, because its disclaimer binds only its own users. Hugging
Face is not a party to it, and Hugging Face's own terms put the responsibility elsewhere (checked on 2026-10-26,
https://huggingface.co/terms-of-service):

- On uploads: "You are solely responsible for the Content you post, publish, display or otherwise make available
  on our Website".
- On downloads: "Any Content you download, access or use from us or another User, is at your own risk", and the
  services and content are provided "as is".

So the disclaimer says what matches those terms. The uploader is responsible for a model and for describing it
accurately, the user is responsible for what they run, and the launcher's labelling is best effort with no
warranty or liability (GPLv3 sections 15 and 16). The docs never say Hugging Face is liable for a model.
Consumer law in some countries limits what a disclaimer can exclude.

## Trademarks

- No Anthropic, Claude, OpenCode or Ornith logos anywhere, and no "Claude" in the project name.
- Third-party names appear only to describe compatibility, for example "creates a profile for Claude Code".
- The name ornith-launcher contains "Ornith", so it is always paired with "unofficial". If Ornith AI objects to
  the name, the project will be renamed.
