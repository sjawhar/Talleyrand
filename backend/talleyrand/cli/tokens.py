"""
Persisted CLI credentials.

Tokens live in a small JSON file the CLI reads/writes on every command: the
access/refresh token pair issued by the backend's /auth endpoints. The file is
created (and kept) at mode 0600 since it is a long-lived bearer credential,
not a session cookie.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

TOKENS_ENV = "TALLY_TOKENS"
DEFAULT_TOKENS_PATH = Path.home() / ".config" / "tally" / "tokens.json"


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def tokens_path(override: str | None = None) -> Path:
    """The tokens file path: an explicit override, then TALLY_TOKENS, then the default."""
    value = override or os.environ.get(TOKENS_ENV)
    return Path(value) if value else DEFAULT_TOKENS_PATH


def load_tokens(path: Path) -> Tokens | None:
    """Read the stored tokens, or None if the file does not exist yet."""
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    data = json.loads(raw)
    return Tokens(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        token_type=data.get("token_type", "bearer"),
    )


def save_tokens(tokens: Tokens, path: Path) -> None:
    """Write the tokens file, creating its directory if needed, at mode 0600.

    Uses os.open with the mode set at creation time (rather than write +
    chmod) so the token pair is never briefly readable at the parent
    directory's default permissions; chmod afterwards covers the case where
    the file already existed with looser permissions.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "token_type": tokens.token_type,
        }
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
