"""Shared CLI error types."""

from __future__ import annotations


class TallyError(Exception):
    """A request or command failed; message is already fit to print to the user."""


class AuthRequiredError(TallyError):
    """No valid session; the user must run `tally login`."""


class RevisionConflict(TallyError):
    """The case was saved elsewhere since it was last read."""

    def __init__(self) -> None:
        super().__init__("This case was edited elsewhere since it was last read.")
