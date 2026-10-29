# Contributing

Thanks for helping. Bug reports, fixes and hardware test results are all welcome.

## Development setup

The tool uses only the Python standard library, so a plain clone is enough:

```
git clone https://github.com/ShourieG/ornith-launcher.git
cd ornith-launcher
./ornith-launcher --version
```

You need Python 3.9 or newer. For linting, install ruff in a virtual environment or with pipx:

```
python3 -m venv .venv && .venv/bin/pip install ruff
```

## Checks before you commit

```
python3 -m unittest discover -s tests -t .                       # unit tests
ORNITH_IT=1 python3 -m unittest discover -s tests/integration -t .   # integration tests (Docker, or a Mac)
ruff check . && ruff format --check .                            # lint and format
shellcheck ornith-launcher                                       # entry script
```

CI runs the unit tests on Linux and macOS with Python 3.9 and the latest Python, plus ruff and shellcheck. The
integration tests need a model file: `ORNITH_IT_MODEL=/path/to/model.gguf`. On Linux they use
Docker and a GPU; on a Mac, `tests.integration.test_native` installs the pinned llama.cpp build into a temporary
folder. Windows is not supported yet.

## Code rules

- The runtime uses the Python standard library only. A pull request that adds a runtime dependency will not be
  merged.
- The `ornith-launcher` entry script is POSIX `sh`, because macOS ships bash 3.2.
- Code must work on Linux and macOS, and stay portable to Windows, which comes later: use `pathlib`, and avoid
  `curses`, `os.pwrite` and other POSIX-only calls outside the platform helpers.
- Pure logic stays free of side effects and has unit tests. Network, process and file-system access goes behind
  small interfaces that tests can fake.
- Error messages say what went wrong and give the exact command or step that fixes it.
- Never commit model weights, chat-template text from model repositories, or other third-party content.
- Tests use made-up uploader and repository names (for example `quant-uploader/`), never real Hugging Face
  accounts.
- Label every benchmark number TESTED, INFERRED or ESTIMATED.

## Sign your commits (DCO)

This project uses the [Developer Certificate of Origin](https://developercertificate.org/) instead of a CLA. Add
a sign-off to every commit:

```
git commit -s
```

That adds a `Signed-off-by: Your Name <you@example.com>` line, which states that you have the right to submit the
change under the project's licence (GPL-3.0-or-later, with the additional terms in [NOTICE](NOTICE)). Your
contribution is licensed under the same terms, and you keep the copyright in it.

## Pull requests

- Keep each pull request to one change, with tests.
- Use a short imperative subject line (72 characters at most) and explain why in the body.
- Add user-visible changes to `CHANGELOG.md` under "Unreleased".
