"""
Tests for the context-size endpoint behind the composer's meter.

The meter must show what the next question would actually send: counted on
the saved case by the model's own counter when it can be asked, and marked as
an estimate when it cannot.
"""

import shlex
import sys
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from talleyrand.core import llm
from talleyrand.core.config import settings
from talleyrand.features.auth_jwt import router as auth_app
from talleyrand.features.graph.dtos import GraphNoId, NodeContentDTO, NodeDTO
from talleyrand.features.research import context_size as context_size_module
from talleyrand.features.research.router import router
from talleyrand.models.user import User

GRAPH_ID = uuid4()
ROOT = uuid4()


def case() -> GraphNoId:
    return GraphNoId(
        nodes=[NodeDTO(id=ROOT)],
        edges=[],
        node_contents=[
            NodeContentDTO(
                id=ROOT,
                query="Does the deal hold?",
                response="It holds, narrowly.",
                selected_model="gpt-6-astra-medium",
                documents=[],
                selection_suggestions=[],
                selections=[],
            )
        ],
        brief="Whether the deal holds.",
    )


@pytest.fixture
def client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[auth_app.require_auth] = lambda: User(id="1", email="u@example.com")
    monkeypatch.setattr(
        context_size_module.manager, "load_effective_graph", AsyncMock(return_value=case())
    )
    return TestClient(app)


def count(client: TestClient, model: str, **headers: str):
    return client.post(
        f"/research/{GRAPH_ID}/context-size",
        json={"model": model, "parentNodeId": str(ROOT), "draftQuery": "", "draftDocuments": []},
        headers=headers,
    )


def test_openai_model_is_counted_against_its_input_limit(client):
    response = count(client, "gpt-6-astra-medium")

    assert response.status_code == 200
    body = response.json()
    assert body["tokens"] > 0
    assert body["maxInputTokens"] == 922_000
    assert body["estimated"] is False


def test_claude_model_is_counted_by_anthropic_with_the_key_the_answer_would_use(
    client, monkeypatch
):
    counter = AsyncMock(return_value=123_456)
    monkeypatch.setattr(context_size_module, "count_prompt_tokens", counter)

    response = count(client, "claude-fable-5-1-high", **{"X-Anthropic-API-Key": "sk-ant"})

    assert response.json() == {"tokens": 123_456, "maxInputTokens": 936_000, "estimated": False}
    window, api_key, _system, prompt = counter.call_args.args
    assert (window.api_model, api_key) == ("claude-fable-5-1", "sk-ant")
    assert "CURRENT QUESTION: [1.1] " in prompt


def test_claude_model_without_a_key_is_estimated(client, monkeypatch):
    counter = AsyncMock()
    monkeypatch.setattr(context_size_module, "count_prompt_tokens", counter)

    response = count(client, "claude-fable-5-1-high")

    body = response.json()
    assert body["estimated"] is True
    assert body["tokens"] > 0
    counter.assert_not_called()


def test_unknown_model_is_a_bad_request(client):
    assert count(client, "gpt-imaginary").status_code == 400


def test_missing_case_is_not_found(client, monkeypatch):
    monkeypatch.setattr(
        context_size_module.manager, "load_effective_graph", AsyncMock(return_value=None)
    )
    assert count(client, "gpt-6-astra-medium").status_code == 404


def test_claude_model_with_server_credentials_is_counted_without_a_key(client, monkeypatch):
    # No X-Anthropic-API-Key header at all — the server's own workload-identity
    # credentials stand in, so the meter must not fall back to an estimate.
    counter = AsyncMock(return_value=99_999)
    monkeypatch.setattr(context_size_module, "count_prompt_tokens", counter)
    monkeypatch.setattr(context_size_module, "server_provider_available", lambda provider: True)

    response = count(client, "claude-fable-5-1-high")

    assert response.json() == {"tokens": 99_999, "maxInputTokens": 936_000, "estimated": False}
    window, api_key, _system, _prompt = counter.call_args.args
    assert (window.api_model, api_key) == ("claude-fable-5-1", "")


@pytest.fixture
def failing_identity_command(monkeypatch):
    """Server credentials configured, with an identity command that fails."""
    failing = shlex.join([sys.executable, "-c", "import sys; sys.exit(1)"])
    monkeypatch.setattr(settings, "anthropic_identity_token_command", failing)
    monkeypatch.setattr(settings, "anthropic_federation_rule_id", "fdrl_test")
    monkeypatch.setattr(settings, "anthropic_organization_id", "org_test")
    llm.server_anthropic_client.cache_clear()
    yield
    llm.server_anthropic_client.cache_clear()


def test_claude_count_whose_identity_command_fails_is_estimated(client, failing_identity_command):
    response = count(client, "claude-fable-5-1-high")

    assert response.status_code == 200
    assert response.json()["estimated"] is True
