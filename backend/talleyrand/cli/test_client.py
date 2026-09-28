"""
A 401 on any request must trigger exactly one refresh, then exactly one
retry of the original request with the new access token — never a retry
loop, and the refreshed tokens must be persisted so the next invocation of
the CLI (a separate process) picks them up.
"""

from pathlib import Path

import httpx
import pytest

from talleyrand.cli.client import TallyClient
from talleyrand.cli.errors import AuthRequiredError
from talleyrand.cli.tokens import Tokens, load_tokens, save_tokens


def test_401_triggers_exactly_one_refresh_and_retry_and_saves_new_tokens(tmp_path: Path):
    tokens_file = tmp_path / "tokens.json"
    save_tokens(Tokens(access_token="old-access", refresh_token="old-refresh"), tokens_file)

    calls: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        calls.append((request.method, request.url.path, auth))
        if request.url.path == "/backend/auth/refresh":
            body = request.read()
            assert b"old-refresh" in body
            return httpx.Response(
                200, json={"access_token": "new-access", "refresh_token": "new-refresh"}
            )
        if request.url.path == "/backend/auth/me":
            if auth != "Bearer new-access":
                return httpx.Response(401, json={"detail": "Invalid access token"})
            return httpx.Response(
                200, json={"id": "u@example.com", "email": "u@example.com", "name": "U"}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = TallyClient(tokens_file=str(tokens_file), transport=httpx.MockTransport(handler))

    result = client.me()

    assert result == {"id": "u@example.com", "email": "u@example.com", "name": "U"}

    me_calls = [c for c in calls if c[1] == "/backend/auth/me"]
    refresh_calls = [c for c in calls if c[1] == "/backend/auth/refresh"]
    assert len(me_calls) == 2  # the failing attempt, then the one retry
    assert len(refresh_calls) == 1  # never a loop

    saved = load_tokens(tokens_file)
    assert saved == Tokens(
        access_token="new-access", refresh_token="new-refresh", token_type="bearer"
    )


def test_second_401_after_refresh_tells_user_to_log_in_again(tmp_path: Path):
    tokens_file = tmp_path / "tokens.json"
    save_tokens(Tokens(access_token="old-access", refresh_token="old-refresh"), tokens_file)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/backend/auth/refresh":
            return httpx.Response(
                200, json={"access_token": "new-access", "refresh_token": "new-refresh"}
            )
        if request.url.path == "/backend/auth/me":
            return httpx.Response(401, json={"detail": "Invalid access token"})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = TallyClient(tokens_file=str(tokens_file), transport=httpx.MockTransport(handler))

    with pytest.raises(AuthRequiredError):
        client.me()


def test_no_stored_tokens_raises_auth_required_on_first_401():
    def handler(request: httpx.Request) -> httpx.Response:
        # No Authorization header was sent (no tokens on disk); the server
        # answers 401 like any unauthenticated request.
        assert "authorization" not in request.headers
        return httpx.Response(401, json={"detail": "Not authenticated"})

    client = TallyClient(
        tokens_file="/nonexistent/tokens.json", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(AuthRequiredError):
        client.me()
