"""Tests for the tokens file: a bearer credential, so it must never be
group/world readable, even if it happens to overwrite a looser-permissioned
existing file."""

import stat
from pathlib import Path

from talleyrand.cli.tokens import Tokens, load_tokens, save_tokens


def test_save_tokens_creates_file_at_mode_0600(tmp_path: Path):
    path = tmp_path / "nested" / "tokens.json"
    save_tokens(Tokens(access_token="a", refresh_token="r"), path)

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600

    loaded = load_tokens(path)
    assert loaded == Tokens(access_token="a", refresh_token="r", token_type="bearer")


def test_save_tokens_tightens_an_existing_looser_permissioned_file(tmp_path: Path):
    path = tmp_path / "tokens.json"
    path.write_text("{}")
    path.chmod(0o644)

    save_tokens(Tokens(access_token="a2", refresh_token="r2"), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_tokens(path).access_token == "a2"


def test_load_tokens_missing_file_returns_none(tmp_path: Path):
    assert load_tokens(tmp_path / "does-not-exist.json") is None
