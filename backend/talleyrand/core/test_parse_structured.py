"""
Tests for parse_structured, the shared structured-output call.

These run the REAL provider SDKs with only the network hop stubbed
(httpx.MockTransport), because the SDKs do meaningful work client-side: the
OpenAI SDK's responses.parse() iterates the tools argument before any
request, so an explicit tools=None crashes with TypeError before the API is
ever reached — a fake client masks exactly that class of bug.
"""

import json
from typing import Any

import httpx
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic import BaseModel

from talleyrand.core import llm
from talleyrand.core.llm import StructuredGenerationError, parse_structured
from talleyrand.core.model_settings import MODEL_WINDOWS, ModelWindow


class NameSchema(BaseModel):
    name: str


OPENAI_WINDOW = ModelWindow(
    provider="openai",
    api_model="gpt-test",
    context_tokens=100_000,
    max_output_tokens=10_000,
    max_input_tokens=90_000,
)

ANTHROPIC_WINDOW = ModelWindow(
    provider="anthropic",
    api_model="claude-test",
    context_tokens=100_000,
    max_output_tokens=10_000,
    max_input_tokens=90_000,
)


def _response_payload(text: str) -> dict[str, Any]:
    """A minimal successful Responses-API payload the SDK parses."""
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1.0,
        "model": "gpt-test",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "metadata": {},
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "parallel_tool_calls": True,
        "temperature": 1.0,
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
    }


def _anthropic_stream(
    content: list[dict[str, Any]], stop_reason: str = "end_turn"
) -> httpx.Response:
    """
    A Messages-API event stream delivering `content` block by block: text as
    text deltas (and citation deltas), every other block whole in its start event.
    """
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-test",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        }
    ]
    for index, block in enumerate(content):
        if block["type"] == "text":
            events.append(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                }
            )
            events.append(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": block["text"]},
                }
            )
            for citation in block.get("citations", []):
                events.append(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "citations_delta", "citation": citation},
                    }
                )
        else:
            events.append({"type": "content_block_start", "index": index, "content_block": block})
        events.append({"type": "content_block_stop", "index": index})
    events.append(
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 5},
        }
    )
    events.append({"type": "message_stop"})
    body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def _text(text: str, **extra: Any) -> dict[str, Any]:
    return {"type": "text", "text": text, **extra}


SEARCH_CALL = {
    "type": "server_tool_use",
    "id": "srvtoolu_1",
    "name": "web_search",
    "input": {"query": "fusion"},
}
SEARCH_RESULT = {
    "type": "web_search_tool_result",
    "tool_use_id": "srvtoolu_1",
    "content": [
        {
            "type": "web_search_result",
            "url": "https://example.com/fusion",
            "title": "Fusion",
            "encrypted_content": "opaque",
            "page_age": None,
        }
    ],
}
CITATION = {
    "type": "web_search_result_location",
    "url": "https://example.com/fusion",
    "title": "Fusion",
    "encrypted_index": "opaque",
    "cited_text": "Fusion",
}


class _Transport:
    """Canned backend recording each outgoing request body. Answers with the
    queued responses in turn, then with `response`."""

    def __init__(self, response: httpx.Response) -> None:
        self.requests: list[dict[str, Any]] = []
        self.response = response
        self.queue: list[httpx.Response] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return self.queue.pop(0) if self.queue else self.response


@pytest.fixture
def openai_backend(monkeypatch) -> _Transport:
    transport = _Transport(httpx.Response(200, json=_response_payload('{"name": "Fusion"}')))

    def client_with_mock_transport(**kwargs):
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport.handler)),
        )

    monkeypatch.setattr(llm, "AsyncOpenAI", client_with_mock_transport)
    return transport


@pytest.fixture
def anthropic_backend(monkeypatch) -> _Transport:
    transport = _Transport(_anthropic_stream([_text('{"name": "Fusion"}')]))

    def client_with_mock_transport(**kwargs):
        return AsyncAnthropic(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport.handler)),
        )

    monkeypatch.setattr(llm, "AsyncAnthropic", client_with_mock_transport)
    return transport


async def _call(**overrides):
    kwargs: dict[str, Any] = {
        "caller": "test",
        "model": OPENAI_WINDOW,
        "api_key": "sk-test",
        "system_prompt": "system",
        "user_content": "user",
        "schema": NameSchema,
    }
    kwargs.update(overrides)
    return await parse_structured(**kwargs)


@pytest.mark.asyncio
async def test_call_without_web_search_or_reasoning_reaches_the_api(openai_backend):
    """The default call (no web search, no reasoning effort) must omit those
    params entirely — an explicit None for tools crashes inside the SDK."""
    parsed = await _call()

    assert parsed == NameSchema(name="Fusion")
    (body,) = openai_backend.requests
    assert "tools" not in body
    assert "reasoning" not in body


@pytest.mark.asyncio
async def test_web_search_and_reasoning_ride_along_when_requested(openai_backend):
    await _call(web_search_enabled=True, reasoning_effort="low")

    (body,) = openai_backend.requests
    assert body["tools"] == [{"type": "web_search"}]
    assert body["reasoning"] == {"effort": "low"}


@pytest.mark.asyncio
async def test_provider_error_becomes_structured_generation_error(openai_backend):
    openai_backend.response = httpx.Response(
        401, json={"error": {"message": "Incorrect API key provided: sk-test."}}
    )

    with pytest.raises(StructuredGenerationError, match="Incorrect API key"):
        await _call()


@pytest.mark.asyncio
async def test_anthropic_call_returns_the_parsed_schema(anthropic_backend):
    parsed = await _call(model=ANTHROPIC_WINDOW)

    assert parsed == NameSchema(name="Fusion")
    (body,) = anthropic_backend.requests
    assert body["model"] == "claude-test"


@pytest.mark.asyncio
async def test_anthropic_call_sends_output_format(anthropic_backend):
    await _call(model=ANTHROPIC_WINDOW)

    (body,) = anthropic_backend.requests
    assert body["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_anthropic_web_search_sends_the_tool(anthropic_backend):
    await _call(model=ANTHROPIC_WINDOW, web_search_enabled=True)

    (body,) = anthropic_backend.requests
    assert body["tools"] == [
        {"type": "web_search_20260209", "name": "web_search", "allowed_callers": ["direct"]}
    ]


@pytest.mark.asyncio
async def test_anthropic_reasoning_effort_sets_adaptive_thinking(anthropic_backend):
    await _call(model=ANTHROPIC_WINDOW, reasoning_effort="high")

    (body,) = anthropic_backend.requests
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "high"


@pytest.mark.asyncio
async def test_anthropic_none_effort_sends_no_thinking_param(anthropic_backend):
    await _call(model=ANTHROPIC_WINDOW, reasoning_effort="none")

    (body,) = anthropic_backend.requests
    assert "thinking" not in body


@pytest.mark.asyncio
async def test_anthropic_refusal_becomes_structured_generation_error(anthropic_backend):
    anthropic_backend.response = _anthropic_stream([], stop_reason="refusal")

    with pytest.raises(StructuredGenerationError, match="declined"):
        await _call(model=ANTHROPIC_WINDOW)


@pytest.mark.asyncio
async def test_anthropic_missing_output_becomes_structured_generation_error(anthropic_backend):
    anthropic_backend.response = _anthropic_stream([])

    with pytest.raises(StructuredGenerationError, match="no usable output"):
        await _call(model=ANTHROPIC_WINDOW)


@pytest.mark.asyncio
async def test_anthropic_provider_error_becomes_structured_generation_error(anthropic_backend):
    anthropic_backend.response = httpx.Response(
        401, json={"error": {"message": "invalid x-api-key"}}
    )

    with pytest.raises(StructuredGenerationError, match="invalid x-api-key"):
        await _call(model=ANTHROPIC_WINDOW)


@pytest.mark.asyncio
async def test_anthropic_error_mid_stream_becomes_structured_generation_error(anthropic_backend):
    error = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    anthropic_backend.response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=f"event: error\ndata: {json.dumps(error)}\n\n".encode(),
    )

    with pytest.raises(StructuredGenerationError, match="Overloaded"):
        await _call(model=ANTHROPIC_WINDOW)


@pytest.mark.asyncio
async def test_anthropic_web_search_answer_is_the_text_after_the_last_search(anthropic_backend):
    # A searched answer arrives as several text blocks: a preamble before the
    # search, then the answer itself, split wherever a citation attaches.
    anthropic_backend.response = _anthropic_stream(
        [
            _text("I'll look that up first."),
            SEARCH_CALL,
            SEARCH_RESULT,
            _text('{"name": ', citations=[CITATION]),
            _text('"Fusion"}'),
        ]
    )

    parsed = await _call(model=ANTHROPIC_WINDOW, web_search_enabled=True)

    assert parsed == NameSchema(name="Fusion")


@pytest.mark.asyncio
async def test_anthropic_search_with_no_answer_after_it_is_no_usable_output(anthropic_backend):
    anthropic_backend.response = _anthropic_stream(
        [_text('{"name": "Stale"}'), SEARCH_CALL, SEARCH_RESULT]
    )

    with pytest.raises(StructuredGenerationError, match="no usable output"):
        await _call(model=ANTHROPIC_WINDOW, web_search_enabled=True)


@pytest.mark.asyncio
async def test_anthropic_answer_cut_off_at_the_output_limit_names_the_limit(anthropic_backend):
    anthropic_backend.response = _anthropic_stream(
        [_text('{"name": "Fus')], stop_reason="max_tokens"
    )

    with pytest.raises(StructuredGenerationError, match="output limit"):
        await _call(model=ANTHROPIC_WINDOW)


@pytest.mark.asyncio
async def test_anthropic_answer_that_is_not_the_schema_becomes_structured_generation_error(
    anthropic_backend,
):
    anthropic_backend.response = _anthropic_stream([_text("Here is a name: Fusion")])

    with pytest.raises(StructuredGenerationError, match="expected format"):
        await _call(model=ANTHROPIC_WINDOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("api_model", ["claude-haiku-4-5", "claude-sonnet-5", "claude-fable-5-1"])
async def test_anthropic_call_asks_for_the_output_room_the_window_reserves(
    anthropic_backend, api_model
):
    # Every Claude window keeps its prompts to max_input_tokens, which leaves 64K
    # for output; a report at "max" effort thinks inside that budget. Asking for
    # more than ~21K is something the SDK refuses on a non-streaming request.
    await _call(model=MODEL_WINDOWS[api_model])

    (body,) = anthropic_backend.requests
    assert body["max_tokens"] == 64_000


@pytest.mark.asyncio
async def test_anthropic_paused_turn_is_resumed_with_the_whole_turn_so_far(anthropic_backend):
    # A server-tool loop that runs long pauses the turn with a search still
    # pending; sending the request again with the assistant's content so far
    # resumes it. A second pause resumes from both responses' content.
    second_call = {**SEARCH_CALL, "id": "srvtoolu_2"}
    second_result = {**SEARCH_RESULT, "tool_use_id": "srvtoolu_2"}
    anthropic_backend.queue = [
        _anthropic_stream([_text("Searching."), SEARCH_CALL], "pause_turn"),
        _anthropic_stream([SEARCH_RESULT, second_call], "pause_turn"),
    ]
    anthropic_backend.response = _anthropic_stream([second_result, _text('{"name": "Fusion"}')])

    parsed = await _call(model=ANTHROPIC_WINDOW, web_search_enabled=True)

    assert parsed == NameSchema(name="Fusion")
    first, second, third = anthropic_backend.requests
    assert len(first["messages"]) == 1
    for resumed, turn_so_far in [
        (second, ["text", "server_tool_use"]),
        (third, ["text", "server_tool_use", "web_search_tool_result", "server_tool_use"]),
    ]:
        user, assistant = resumed["messages"]
        assert user == first["messages"][0]
        assert assistant["role"] == "assistant"
        assert [block["type"] for block in assistant["content"]] == turn_so_far


@pytest.mark.asyncio
async def test_anthropic_turn_that_keeps_pausing_names_the_pause(anthropic_backend):
    anthropic_backend.response = _anthropic_stream([SEARCH_CALL], "pause_turn")
    anthropic_backend.queue = [_anthropic_stream([SEARCH_CALL], "pause_turn") for _ in range(10)]

    with pytest.raises(StructuredGenerationError, match="paused"):
        await _call(model=ANTHROPIC_WINDOW, web_search_enabled=True)

    assert len(anthropic_backend.requests) == 1 + llm.ANTHROPIC_PAUSE_CONTINUATIONS
