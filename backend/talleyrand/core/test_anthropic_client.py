"""
Tests for anthropic_client: which Anthropic client an API call gets when it
carries no key, and — end to end — that a streamed answer sent through the
server's workload-identity credentials actually carries the exchanged bearer
token on the wire.
"""

import shlex
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from anthropic import AsyncAnthropic
from pydantic import BaseModel

from talleyrand.core import llm, server_credentials
from talleyrand.core.config import settings
from talleyrand.core.llm import (
    MissingApiKeyError,
    StructuredGenerationError,
    TextChunk,
    anthropic_client,
    fit_to_context,
    parse_structured,
    stream_text,
)
from talleyrand.core.model_settings import MODEL_CONFIGS, MODEL_WINDOWS


def test_a_given_key_is_used_directly():
    client = anthropic_client("sk-ant-explicit")
    assert client.api_key == "sk-ant-explicit"


def test_no_key_and_no_server_credentials_raises():
    assert server_credentials.anthropic_server_credentials() is None
    with pytest.raises(MissingApiKeyError):
        anthropic_client("")


@dataclass
class FederatedBackend:
    """What the stubbed token endpoint and Messages API saw, and how the endpoint answers."""

    token_requests: list[httpx.Request] = field(default_factory=list)
    messages_requests: list[httpx.Request] = field(default_factory=list)
    token_status: int = 200
    token_body: dict[str, Any] = field(
        default_factory=lambda: {"access_token": "exchanged-bearer-token", "expires_in": 600}
    )


@pytest.fixture
def federated_credentials(monkeypatch):
    """
    Server-held Anthropic credentials configured end to end: settings point at
    a real (harmless) identity-token command, and both the federation token
    endpoint and the eventual Messages API call are stubbed with
    httpx.MockTransport so the whole exchange runs without any network access.
    """
    monkeypatch.setattr(settings, "anthropic_identity_token_command", "echo test-identity-jwt")
    monkeypatch.setattr(settings, "anthropic_federation_rule_id", "rule_1")
    monkeypatch.setattr(settings, "anthropic_organization_id", "org_1")
    llm.server_anthropic_client.cache_clear()

    backend = FederatedBackend()
    token_requests = backend.token_requests

    def token_handler(request: httpx.Request) -> httpx.Response:
        token_requests.append(request)
        return httpx.Response(backend.token_status, json=backend.token_body)

    # WorkloadIdentityCredentials builds its own sync httpx.Client for the
    # token-endpoint POST; patching the class it calls (not an instance) is
    # what lets a cached, already-constructed credentials object pick it up.
    real_client_cls = httpx.Client

    def mock_client(**kwargs):
        return real_client_cls(transport=httpx.MockTransport(token_handler))

    monkeypatch.setattr(httpx, "Client", mock_client)

    def messages_handler(request: httpx.Request) -> httpx.Response:
        backend.messages_requests.append(request)
        body = (
            b"event: message_start\n"
            b'data: {"type":"message_start","message":{"id":"msg_1","type":"message",'
            b'"role":"assistant","content":[],"model":"claude-haiku-4-5","stop_reason":null,'
            b'"stop_sequence":null,"usage":{"input_tokens":10,"output_tokens":0}}}\n\n'
            b"event: content_block_start\n"
            b'data: {"type":"content_block_start","index":0,'
            b'"content_block":{"type":"text","text":""}}\n\n'
            b"event: content_block_delta\n"
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"text_delta","text":"Hello."}}\n\n'
            b"event: content_block_stop\n"
            b'data: {"type":"content_block_stop","index":0}\n\n'
            b"event: message_delta\n"
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn",'
            b'"stop_sequence":null},"usage":{"output_tokens":5}}\n\n'
            b"event: message_stop\n"
            b'data: {"type":"message_stop"}\n\n'
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body, request=request
        )

    def client_with_mock_transport(**kwargs):
        return AsyncAnthropic(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(messages_handler)),
        )

    monkeypatch.setattr(llm, "AsyncAnthropic", client_with_mock_transport)

    yield backend

    # The next test must not inherit this client, or its credentials, once
    # monkeypatch restores the settings.
    llm.server_anthropic_client.cache_clear()


async def _stream_hello() -> str:
    chunks = [
        chunk
        async for chunk in await stream_text(
            caller="test",
            model=MODEL_CONFIGS["claude-haiku-4-5"],
            api_key="",
            instructions="Be helpful.",
            cached_prefix="",
            user_prompt="Hello?",
            pdf_documents=[],
            web_search_enabled=False,
            verbosity="low",
        )
    ]
    return "".join(c.text for c in chunks if isinstance(c, TextChunk))


@pytest.mark.asyncio
async def test_keyless_calls_share_one_token_exchange(federated_credentials):
    assert await _stream_hello() == "Hello."
    assert await _stream_hello() == "Hello."

    # The exchanged access token outlives a single call: the second answer
    # reuses it instead of running the identity command and exchanging again.
    assert len(federated_credentials.messages_requests) == 2
    assert len(federated_credentials.token_requests) == 1


@pytest.mark.asyncio
async def test_streamed_answer_with_no_key_carries_the_exchanged_bearer_token(
    federated_credentials,
):
    assert await _stream_hello() == "Hello."

    # The identity-token command ran and the JWT was exchanged for a bearer
    # token, which then rode along on the actual Messages API request.
    assert len(federated_credentials.token_requests) == 1
    (messages_request,) = federated_credentials.messages_requests
    assert messages_request.headers["Authorization"] == "Bearer exchanged-bearer-token"


@pytest.mark.asyncio
async def test_a_key_sent_with_the_request_wins_over_server_held_credentials(
    federated_credentials,
):
    chunks = [
        chunk
        async for chunk in await stream_text(
            caller="test",
            model=MODEL_CONFIGS["claude-haiku-4-5"],
            api_key="sk-ant-from-the-request",
            instructions="Be helpful.",
            cached_prefix="",
            user_prompt="Hello?",
            pdf_documents=[],
            web_search_enabled=False,
            verbosity="low",
        )
    ]
    assert "".join(c.text for c in chunks if isinstance(c, TextChunk)) == "Hello."

    # The person's own key is what reaches Anthropic; the server's identity is
    # never exchanged or attached.
    (messages_request,) = federated_credentials.messages_requests
    assert messages_request.headers["x-api-key"] == "sk-ant-from-the-request"
    assert "authorization" not in messages_request.headers
    assert federated_credentials.token_requests == []


class Name(BaseModel):
    name: str


async def _keyless_structured_call() -> Name:
    return await parse_structured(
        caller="test",
        model=MODEL_WINDOWS["claude-haiku-4-5"],
        api_key="",
        system_prompt="Name it.",
        user_content="Something.",
        schema=Name,
    )


@pytest.mark.asyncio
async def test_failing_identity_command_fails_the_call_with_its_own_error(
    federated_credentials, monkeypatch
):
    runs = sys.executable, "-c", "import sys; sys.stderr.write('not signed in'); sys.exit(3)"
    monkeypatch.setattr(settings, "anthropic_identity_token_command", shlex.join(runs))

    with pytest.raises(StructuredGenerationError, match="exited 3: not signed in"):
        await _keyless_structured_call()

    assert federated_credentials.token_requests == []
    assert federated_credentials.messages_requests == []


@pytest.mark.asyncio
async def test_refused_token_exchange_fails_the_call_with_the_endpoint_error(
    federated_credentials,
):
    federated_credentials.token_status = 401
    federated_credentials.token_body = {
        "error": "invalid_grant",
        "error_description": "federation rule does not match",
    }

    with pytest.raises(StructuredGenerationError, match="Token exchange failed"):
        await _keyless_structured_call()

    assert federated_credentials.messages_requests == []


@pytest.mark.asyncio
async def test_failing_identity_command_while_fitting_a_prompt_names_its_error(
    federated_credentials, monkeypatch
):
    # Reports and kickstart count the prompt before the structured call, so the
    # command's failure surfaces here first and must reach the reader the same way.
    runs = sys.executable, "-c", "import sys; sys.stderr.write('not signed in'); sys.exit(3)"
    monkeypatch.setattr(settings, "anthropic_identity_token_command", shlex.join(runs))

    with pytest.raises(StructuredGenerationError, match="exited 3: not signed in"):
        await fit_to_context(
            model=MODEL_WINDOWS["claude-haiku-4-5"],
            api_key="",
            system_prompt="Report.",
            keep_before="Brief.",
            trimmable="Documents.",
            keep_after="Questions.",
        )
