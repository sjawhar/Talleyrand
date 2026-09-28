"""
Thin HTTP client for the Talleyrand backend.

The app mounts under root_path "/backend", so the default base URL points
there. Every request carries the stored access token; a 401 triggers exactly
one refresh-and-retry (never a loop) before giving up and telling the caller
to sign in again. The CLI never sends model-key headers: model credentials
are held by the server.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from talleyrand.cli.errors import AuthRequiredError, RevisionConflict, TallyError
from talleyrand.cli.tokens import Tokens, load_tokens, save_tokens, tokens_path

URL_ENV = "TALLY_URL"
DEFAULT_URL = "http://127.0.0.1:8000/backend"


def base_url(override: str | None = None) -> str:
    value = override or os.environ.get(URL_ENV) or DEFAULT_URL
    return value.rstrip("/")


# The most of an unstructured error body worth printing: an HTML error page or
# an echoed payload is noise past this point.
MAX_ERROR_TEXT = 300
# Validation problems listed before the rest are only counted.
MAX_VALIDATION_ERRORS = 5


def _validation_summary(errors: list[Any]) -> str:
    """FastAPI's 422 detail as "location: message" lines. The input each
    entry echoes is left out: it can be the whole request body."""
    lines = []
    for error in errors[:MAX_VALIDATION_ERRORS]:
        loc = [str(part) for part in error.get("loc", [])]
        if loc and loc[0] in ("body", "query", "path", "header"):
            loc = loc[1:]
        lines.append(f"{'.'.join(loc) or 'request'}: {error.get('msg', 'invalid')}")
    if len(errors) > MAX_VALIDATION_ERRORS:
        lines.append(f"… and {len(errors) - MAX_VALIDATION_ERRORS} more")
    return "; ".join(lines)


def _truncated(text: str) -> str:
    return text if len(text) <= MAX_ERROR_TEXT else text[:MAX_ERROR_TEXT] + "…"


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return _truncated(response.text) or response.reason_phrase
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list) and all(isinstance(error, dict) for error in detail):
        return _validation_summary(detail)
    return _truncated(response.text)


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise TallyError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {_error_detail(response)}"
        )


class TallyClient:
    """Authenticated client for one CLI invocation."""

    def __init__(
        self,
        url: str | None = None,
        tokens_file: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.tokens_path: Path = tokens_path(tokens_file)
        self.tokens: Tokens | None = load_tokens(self.tokens_path)
        self.http = httpx.Client(base_url=base_url(url), timeout=60.0, transport=transport)

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> TallyClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _auth_headers(self) -> dict[str, str]:
        if self.tokens is None:
            return {}
        return {"Authorization": f"Bearer {self.tokens.access_token}"}

    def _refresh(self) -> None:
        if self.tokens is None:
            raise AuthRequiredError("Not signed in. Run `tally login`.")
        response = self.http.post(
            "/auth/refresh", json={"refresh_token": self.tokens.refresh_token}
        )
        if response.status_code != 200:
            raise AuthRequiredError("Session expired. Run `tally login`.")
        data = response.json()
        self.tokens = Tokens(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            token_type=data.get("token_type", "bearer"),
        )
        save_tokens(self.tokens, self.tokens_path)

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send one authenticated request; refresh once and retry on a 401."""
        response = self.http.request(method, path, headers=self._auth_headers(), **kwargs)
        if response.status_code == 401:
            self._refresh()
            response = self.http.request(method, path, headers=self._auth_headers(), **kwargs)
            if response.status_code == 401:
                raise AuthRequiredError("Not authenticated. Run `tally login`.")
        return response

    # -- auth -----------------------------------------------------------

    def login(self) -> dict[str, Any]:
        response = self.http.post("/auth/local-login")
        _raise_for_status(response)
        data = response.json()
        self.tokens = Tokens(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            token_type=data.get("token_type", "bearer"),
        )
        save_tokens(self.tokens, self.tokens_path)
        return self.me()

    def me(self) -> dict[str, Any]:
        response = self.request("GET", "/auth/me")
        _raise_for_status(response)
        return response.json()

    # -- cases ------------------------------------------------------------

    def list_cases(self) -> list[dict[str, Any]]:
        response = self.request("GET", "/graph/")
        _raise_for_status(response)
        return response.json()

    def import_case(self, payload: dict[str, Any]) -> str:
        response = self.request("POST", "/graph/import", json=payload)
        _raise_for_status(response)
        return response.json()["id"]

    def get_graph(self, case_id: str) -> dict[str, Any]:
        response = self.request("GET", f"/graph/{case_id}")
        _raise_for_status(response)
        return response.json()

    def save_graph(self, case_id: str, graph: dict[str, Any]) -> int:
        response = self.request("PUT", f"/graph/{case_id}", json=graph)
        if response.status_code == 409:
            raise RevisionConflict()
        _raise_for_status(response)
        return response.json()["revision"]

    # -- generation ---------------------------------------------------------

    def generate_answer(
        self,
        case_id: str,
        node_id: str,
        *,
        background: bool = False,
        web_search_enabled: bool = True,
        verbosity: str = "low",
        cheat_sheet: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        """Start an answer job, or return the node's live job or its finished,
        not yet incorporated answer. A failed job is replaced by a fresh one,
        so learn a job's outcome from get_job, not by posting again."""
        response = self.request(
            "POST",
            f"/research/{case_id}/node/{node_id}/generate",
            json={
                "background": background,
                "webSearchEnabled": web_search_enabled,
                "verbosity": verbosity,
                "cheatSheet": cheat_sheet,
                "force": force,
            },
        )
        _raise_for_status(response)
        return response.json()

    def suggest_followups(
        self, case_id: str, node_id: str, *, request_more: bool = False
    ) -> dict[str, Any]:
        response = self.request(
            "POST",
            f"/research/{case_id}/node/{node_id}/suggest-followups",
            json={"requestMore": request_more},
        )
        _raise_for_status(response)
        return response.json()

    def get_job(self, case_id: str, job_id: str) -> dict[str, Any] | None:
        """A job's record, or None once it has retired (the case holds its result)."""
        response = self.request("GET", f"/research/{case_id}/jobs/{job_id}")
        if response.status_code == 404:
            return None
        _raise_for_status(response)
        return response.json()

    def server_providers(self) -> dict[str, bool] | None:
        """Which model providers the server holds its own credentials for
        (GET /config), or None from a server that does not publish them."""
        response = self.http.get("/config")
        if response.status_code == 404:
            return None
        _raise_for_status(response)
        return response.json()["providers"]

    def report(self, graph: dict[str, Any], guidance: str = "") -> str:
        response = self.request(
            "POST", "/research/report", json={"graph": graph, "guidance": guidance}
        )
        _raise_for_status(response)
        return response.json()["markdown"]
