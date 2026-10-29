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
- On macOS, the llama.cpp release archive is unpacked only after every entry is checked: no absolute paths, no
  `..`, no device files and no link that points outside its folder.
- Installers for optional tools come from their official URLs over HTTPS, and the launcher shows the command and asks
  before running it.
- The launcher never runs `sudo`. When a step needs root, it prints the command for you to run.
- Config files of other tools are backed up before any change, and the launcher asks first.
- Uninstall deletes only files and images the launcher recorded as its own, in `installed.json` and the profiles.
  Models found elsewhere, files it did not write and image tags it did not pull are never removed.
- On GNOME, the launcher marks only the desktop icon it just wrote as trusted (`gio set ... metadata::trusted
  true`), so the icon starts with one click.
- The server window and the `logs` command show llama-server's own log: through `docker logs` on Linux, and from
  a log file in the launcher's state folder on macOS. The launcher never turns on llama-server's verbose or
  prompt logging.
- On macOS the server is a normal process. The launcher records its process id in a run file and stops or
  reuses that process only while its command line is still the launcher's llama.cpp build, so a stale run file
  never makes it signal another program.
- The optional chat log runs a small proxy on the profile's port, on `127.0.0.1` only; the server then listens on
  another port on `127.0.0.1`, picked by Docker on Linux and by the launcher on macOS. The proxy keeps the last 10
  requests and replies in memory only and never writes them to disk. The chat log window reads them with a random
  token kept in a run file only you can read (mode 600). The proxy ends when the server stops. Replacing or
  closing a chat log window or proxy only ever stops a process whose command line shows it is that profile's.
- No secrets are stored. An optional `HF_TOKEN` is read from the environment and never saved.
