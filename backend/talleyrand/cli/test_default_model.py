"""
The model `ask` and `accept` inherit must be one the server can run: `tally`
sends no model keys, so only the providers the server holds credentials for
(GET /config) are usable. A model on another provider moves to the same
thinking position on a usable one, as the browser's resolveUsableModel does;
with no usable provider the command refuses before saving anything.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from talleyrand.cli import commands
from talleyrand.cli.client import TallyClient
from talleyrand.cli.errors import TallyError
from talleyrand.cli.graph_ops import default_model, usable_model
from talleyrand.cli.tokens import Tokens, save_tokens

ANTHROPIC_ONLY = {"openai": False, "anthropic": True}


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("gpt-6-astra-high", "claude-fable-5-1-high"),
        ("gpt-6-astra-medium", "claude-fable-5-1-medium"),
        ("gpt-5.6-luna", "claude-haiku-4-5"),
        # A retired id resolves to its replacement before it moves.
        ("gpt-5.5-xhigh", "claude-fable-5-1-max"),
        ("claude-sonnet-5", "claude-sonnet-5"),
    ],
)
def test_a_model_moves_to_the_same_position_on_the_provider_the_server_holds(stored, expected):
    assert usable_model(stored, ANTHROPIC_ONLY) == expected


def test_the_first_usable_provider_wins_when_the_server_holds_both():
    assert usable_model("claude-fable-5-1-high", {"openai": True, "anthropic": True}) == (
        "claude-fable-5-1-high"
    )
    assert usable_model("claude-fable-5-1-max", {"openai": True, "anthropic": False}) == (
        "gpt-6-astra-max"
    )


def test_no_usable_provider_is_refused_naming_the_model():
    with pytest.raises(TallyError, match="claude-fable-5-1-high"):
        usable_model("claude-fable-5-1-high", {"openai": False, "anthropic": False})


def test_an_unknown_stored_model_is_refused_naming_it():
    with pytest.raises(TallyError, match="claude-imaginary-9"):
        usable_model("claude-imaginary-9", ANTHROPIC_ONLY)


def test_an_unknown_inherited_model_starts_from_the_default_with_a_note(
    capsys: pytest.CaptureFixture[str],
):
    # One question asked with a mistyped --model must not leave every later
    # `ask` and `accept` on the case refusing.
    graph = _graph("case-typo")
    graph["nodeContents"][0]["selectedModel"] = "claude-sonnet-5-medium"

    assert default_model(graph, ANTHROPIC_ONLY) == "claude-fable-5-1-high"
    assert "claude-sonnet-5-medium" in capsys.readouterr().err


def _graph(case_id: str) -> dict[str, Any]:
    root = "11111111-1111-1111-1111-111111111111"
    return {
        "id": case_id,
        "name": "A case",
        "revision": 3,
        "nodes": [{"id": root}],
        "edges": [],
        "nodeContents": [
            {
                "id": root,
                "query": "Root question?",
                "response": "Root answer.",
                "selectedModel": "gpt-6-astra-high",
                "documents": [],
                "selectionSuggestions": [],
                "selections": [],
            }
        ],
        "brief": "",
        "caseDocuments": [],
        "suggestions": [],
        "declinedQuestions": [],
        "readHistory": [],
    }


def _ask_args(case_id: str) -> argparse.Namespace:
    return argparse.Namespace(
        case=case_id,
        text="What next?",
        under="1",
        model=None,
        no_wait=True,
        no_mark_read=True,
        timeout=5.0,
        json=True,
    )


def _client(tmp_path: Path, handler) -> TallyClient:
    tokens_file = tmp_path / "tokens.json"
    save_tokens(Tokens(access_token="access", refresh_token="refresh"), tokens_file)
    return TallyClient(tokens_file=str(tokens_file), transport=httpx.MockTransport(handler))


def test_ask_saves_the_inherited_gpt_model_as_its_claude_counterpart(tmp_path: Path):
    case_id = "case-model"
    saved: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        if (method, path) == ("GET", "/backend/config"):
            return httpx.Response(200, json={"providers": ANTHROPIC_ONLY})
        if (method, path) == ("GET", f"/backend/graph/{case_id}"):
            return httpx.Response(200, json=_graph(case_id))
        if (method, path) == ("PUT", f"/backend/graph/{case_id}"):
            saved.append(json.loads(request.content))
            return httpx.Response(200, json={"id": case_id, "revision": 4})
        if method == "POST" and path.endswith("/generate"):
            return httpx.Response(200, json={"id": "job-1", "status": "running"})
        raise AssertionError(f"unexpected request: {method} {path}")

    commands.cmd_ask(_client(tmp_path, handler), _ask_args(case_id))

    (body,) = saved
    new_content = body["nodeContents"][-1]
    assert new_content["query"] == "What next?"
    assert new_content["selectedModel"] == "claude-fable-5-1-high"


def test_ask_on_a_server_with_no_usable_provider_saves_nothing(tmp_path: Path):
    case_id = "case-no-provider"
    writes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        if (method, path) == ("GET", "/backend/config"):
            return httpx.Response(200, json={"providers": {"openai": False, "anthropic": False}})
        if (method, path) == ("GET", f"/backend/graph/{case_id}"):
            return httpx.Response(200, json=_graph(case_id))
        writes.append(f"{method} {path}")
        return httpx.Response(200, json={})

    with pytest.raises(TallyError, match="gpt-6-astra-high"):
        commands.cmd_ask(_client(tmp_path, handler), _ask_args(case_id))

    assert writes == []
