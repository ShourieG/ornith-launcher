"""Error types shared across the package."""

from __future__ import annotations


class OrnithLauncherError(Exception):
    """Base class for errors reported to the user.

    `hint` tells the user how to fix the problem, ideally as an exact command.
    """

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class DataFileError(OrnithLauncherError):
    """A data file shipped with the package is missing or malformed.

    This points to a broken install or a bug in the release, not to the user's setup.
    """
