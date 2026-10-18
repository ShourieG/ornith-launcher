# Security policy

## Reporting a vulnerability

Report vulnerabilities privately through GitHub: open the repository's Security tab and choose "Report a
vulnerability". Please do not open a public issue for a security problem.

Only the latest release receives fixes.

## Security properties

ornith-launcher is built to keep the properties below. A case where it does not is a bug worth reporting.

- Model servers listen on `127.0.0.1` only. The llama.cpp API has no authentication by default.
- Docker images are pinned by digest, and release binaries and model files are verified by sha256 before use.
  Model downloads are pinned to a fixed Hugging Face revision.
- Installers for optional tools come from their official URLs over HTTPS, and the launcher shows the command and asks
  before running it.
- The launcher never runs `sudo`. When a step needs root, it prints the command for you to run.
- Config files of other tools are backed up before any change, and the launcher asks first.
- No secrets are stored. An optional `HF_TOKEN` is read from the environment and never saved.
