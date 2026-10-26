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
- Uninstall deletes only files and images the launcher recorded as its own, in `installed.json` and the profiles.
  Models found elsewhere, files it did not write and image tags it did not pull are never removed.
- On GNOME, the launcher marks only the desktop icon it just wrote as trusted (`gio set ... metadata::trusted
  true`), so the icon starts with one click.
- The server window and the `logs` command show llama-server's own log through `docker logs`. The launcher never
  turns on llama-server's verbose or prompt logging.
- The optional chat log runs a small proxy on the profile's port, on `127.0.0.1` only; the server then listens on
  a port Docker picks, also on `127.0.0.1`. The proxy keeps the last 10 requests and replies in memory only and
  never writes them to disk. The chat log window reads them with a random token kept in a run file only you can
  read (mode 600). The proxy ends when the server stops. Replacing or closing a chat log window or proxy only
  ever stops a process whose command line shows it is that profile's.
- No secrets are stored. An optional `HF_TOKEN` is read from the environment and never saved.
