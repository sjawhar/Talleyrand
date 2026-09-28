"""
Tests for the commands that write to a case: `ask` must save the new question
before starting generation, at the revision it read; `decline` must record
the reason on the declined-questions list.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import httpx
import pytest

from talleyrand.cli import commands
from talleyrand.cli.client import TallyClient
from talleyrand.cli.tokens import Tokens, save_tokens


def _client(tmp_path: Path, handler) -> TallyClient:
    tokens_file = tmp_path / "tokens.json"
    save_tokens(Tokens(access_token="access", refresh_token="refresh"), tokens_file)
    return TallyClient(tokens_file=str(tokens_file), transport=httpx.MockTransport(handler))


def _base_graph(case_id: str, revision: int = 3) -> dict[str, Any]:
    return {
        "id": case_id,
        "name": "A case",
        "revision": revision,
        "nodes": [{"id": "11111111-1111-1111-1111-111111111111"}],
        "edges": [],
        "nodeContents": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "query": "Root question?",
                "response": "Root answer.",
                "selectedModel": "claude-fable-5-1-max",
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


def test_ask_saves_new_node_and_edge_at_read_revision_then_calls_generate(tmp_path: Path):
    case_id = "case-1"
    graph = _base_graph(case_id, revision=3)
    requests: list[tuple[str, str, dict | None]] = []

    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        body = None
        if request.content:
            import json as _json

            body = _json.loads(request.content)
        requests.append((method, path, body))

        if (method, path) == ("GET", "/backend/config"):
            return httpx.Response(200, json={"providers": {"openai": False, "anthropic": True}})
        if (method, path) == ("GET", f"/backend/research/{case_id}/jobs/job-1"):
            return httpx.Response(200, json={"id": "job-1", "status": "done"})
        if (method, path) == ("GET", f"/backend/graph/{case_id}"):
            if "new_node_id" in state:
                answered = _base_graph(case_id, revision=4)
                answered["nodes"].append({"id": state["new_node_id"]})
                answered["nodeContents"].append(
                    {
                        "id": state["new_node_id"],
                        "query": "What comes next?",
                        "response": "The next thing.",
                        "selectedModel": "claude-fable-5-1-max",
                        "documents": [],
                        "selectionSuggestions": [],
                        "selections": [],
                        "sources": [],
                        "sourcesFound": 0,
                    }
                )
                return httpx.Response(200, json=answered)
            return httpx.Response(200, json=graph)
        if (method, path) == ("PUT", f"/backend/graph/{case_id}"):
            if "new_node_id" not in state:
                # First save: the new question, its content, and the parent edge.
                assert body["revision"] == 3
                assert len(body["nodes"]) == 2
                new_node_id = body["nodes"][1]["id"]
                state["new_node_id"] = new_node_id
                new_content = next(c for c in body["nodeContents"] if c["id"] == new_node_id)
                assert new_content["query"] == "What comes next?"
                assert new_content["response"] == ""
                assert new_content["selectedModel"] == "claude-fable-5-1-max"  # inherited default
                edge = next(e for e in body["edges"] if e["target"] == new_node_id)
                assert edge["source"] == "11111111-1111-1111-1111-111111111111"
                return httpx.Response(200, json={"id": case_id, "revision": 4})
            # Second save: recording the read event, at the revision the
            # answered graph was read at.
            assert body["revision"] == 4
            read_events = [e for e in body["readHistory"] if e["nodeId"] == state["new_node_id"]]
            assert len(read_events) == 1
            state["read_recorded"] = True
            return httpx.Response(200, json={"id": case_id, "revision": 5})
        if path.startswith(f"/backend/research/{case_id}/node/") and path.endswith("/generate"):
            node_id = path.split("/node/")[1].split("/generate")[0]
            assert body == {
                "background": False,
                "webSearchEnabled": True,
                "verbosity": "low",
                "cheatSheet": False,
                "force": False,
            }
            return httpx.Response(
                200,
                json={
                    "id": "job-1",
                    "kind": "answer",
                    "nodeId": node_id,
                    "status": "running",
                    "answer": None,
                    "sources": [],
                    "sourcesFound": 0,
                    "suggestions": [],
                },
            )
        raise AssertionError(f"unexpected request: {method} {path}")

    client = _client(tmp_path, handler)
    args = argparse.Namespace(
        case=case_id,
        text="What comes next?",
        under="1",
        model=None,
        no_wait=False,
        no_mark_read=False,
        timeout=5.0,
        json=True,
    )

    commands.cmd_ask(client, args)

    assert state.get("read_recorded") is True

    # GET, then PUT (save), then POST generate, then GET the job until it is
    # done, then GET (the answer), then PUT (read event) — in that order.
    def route(path: str) -> str:
        return next(r for r in ("config", "graph", "generate", "jobs") if f"/{r}" in path)

    methods_and_routes = [(m, route(p)) for m, p, _ in requests]
    assert methods_and_routes == [
        ("GET", "graph"),
        ("GET", "config"),
        ("PUT", "graph"),
        ("POST", "generate"),
        ("GET", "jobs"),
        ("GET", "graph"),
        ("PUT", "graph"),
    ]


def test_ask_with_no_mark_read_records_no_read_event(tmp_path: Path):
    case_id = "case-1b"
    graph = _base_graph(case_id, revision=3)
    state: dict[str, Any] = {}
    put_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal put_count
        method, path = request.method, request.url.path
        body = None
        if request.content:
            import json as _json

            body = _json.loads(request.content)

        if (method, path) == ("GET", "/backend/config"):
            return httpx.Response(200, json={"providers": {"openai": False, "anthropic": True}})
        if (method, path) == ("GET", f"/backend/research/{case_id}/jobs/job-1"):
            return httpx.Response(200, json={"id": "job-1", "status": "done"})
        if (method, path) == ("GET", f"/backend/graph/{case_id}"):
            if "new_node_id" in state:
                answered = _base_graph(case_id, revision=4)
                answered["nodes"].append({"id": state["new_node_id"]})
                answered["nodeContents"].append(
                    {
                        "id": state["new_node_id"],
                        "query": "Another one?",
                        "response": "An answer.",
                        "selectedModel": "claude-fable-5-1-max",
                        "documents": [],
                        "selectionSuggestions": [],
                        "selections": [],
                    }
                )
                return httpx.Response(200, json=answered)
            return httpx.Response(200, json=graph)
        if (method, path) == ("PUT", f"/backend/graph/{case_id}"):
            put_count += 1
            new_node_id = body["nodes"][1]["id"]
            state["new_node_id"] = new_node_id
            return httpx.Response(200, json={"id": case_id, "revision": 4})
        if path.startswith(f"/backend/research/{case_id}/node/") and path.endswith("/generate"):
            return httpx.Response(
                200,
                json={
                    "id": "job-1",
                    "kind": "answer",
                    "nodeId": state.get("new_node_id"),
                    "status": "running",
                    "answer": None,
                    "sources": [],
                    "sourcesFound": 0,
                    "suggestions": [],
                },
            )
        raise AssertionError(f"unexpected request: {method} {path}")

    client = _client(tmp_path, handler)
    args = argparse.Namespace(
        case=case_id,
        text="Another one?",
        under=None,
        model=None,
        no_wait=False,
        no_mark_read=True,
        timeout=5.0,
        json=True,
    )

    commands.cmd_ask(client, args)

    # Only the question save — no second PUT for a read event.
    assert put_count == 1


def test_ask_reads_the_answer_from_the_case_once_its_job_record_has_retired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    # A browser with the case open saves the answer as soon as it arrives,
    # which retires the job's record: the lookup 404s and the case holds it.
    case_id = "case-retired"
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        if (method, path) == ("GET", "/backend/config"):
            return httpx.Response(200, json={"providers": {"openai": False, "anthropic": True}})
        if (method, path) == ("GET", f"/backend/graph/{case_id}"):
            graph = _base_graph(case_id, revision=4 if "node_id" in state else 3)
            if "node_id" in state:
                graph["nodes"].append({"id": state["node_id"]})
                graph["nodeContents"].append(
                    {**graph["nodeContents"][0], "id": state["node_id"], "response": "Saved."}
                )
            return httpx.Response(200, json=graph)
        if (method, path) == ("PUT", f"/backend/graph/{case_id}"):
            import json as _json

            state["node_id"] = _json.loads(request.content)["nodes"][1]["id"]
            return httpx.Response(200, json={"id": case_id, "revision": 4})
        if method == "POST" and path.endswith("/generate"):
            return httpx.Response(200, json={"id": "job-gone", "status": "running"})
        if (method, path) == ("GET", f"/backend/research/{case_id}/jobs/job-gone"):
            return httpx.Response(404, json={"detail": "Not found"})
        raise AssertionError(f"unexpected request: {method} {path}")

    args = argparse.Namespace(
        case=case_id,
        text="Next?",
        under=None,
        model=None,
        no_wait=False,
        no_mark_read=True,
        timeout=30.0,
        json=False,
    )
    commands.cmd_ask(_client(tmp_path, handler), args)

    assert capsys.readouterr().out.strip() == "Saved."


def test_read_marks_the_question_read_at_the_read_revision(tmp_path: Path):
    case_id = "case-read"
    graph = _base_graph(case_id, revision=7)
    put_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        if (method, path) == ("GET", f"/backend/graph/{case_id}"):
            return httpx.Response(200, json=graph)
        if (method, path) == ("PUT", f"/backend/graph/{case_id}"):
            import json as _json

            body = _json.loads(request.content)
            put_bodies.append(body)
            return httpx.Response(200, json={"id": case_id, "revision": 8})
        raise AssertionError(f"unexpected request: {method} {path}")

    client = _client(tmp_path, handler)
    args = argparse.Namespace(case=case_id, number="1", no_mark_read=False, json=True)

    commands.cmd_read(client, args)

    assert len(put_bodies) == 1
    read_history = put_bodies[0]["readHistory"]
    assert len(read_history) == 1
    assert read_history[0]["nodeId"] == "11111111-1111-1111-1111-111111111111"
    assert put_bodies[0]["revision"] == 7


def test_read_with_no_mark_read_records_no_read_event(tmp_path: Path):
    case_id = "case-read-2"
    graph = _base_graph(case_id, revision=7)

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        if (method, path) == ("GET", f"/backend/graph/{case_id}"):
            return httpx.Response(200, json=graph)
        raise AssertionError(f"no save should be attempted with --no-mark-read: {method} {path}")

    client = _client(tmp_path, handler)
    args = argparse.Namespace(case=case_id, number="1", no_mark_read=True, json=True)

    commands.cmd_read(client, args)  # would raise via the handler if it tried to save


def test_read_already_read_question_does_not_duplicate_the_event(tmp_path: Path):
    case_id = "case-read-3"
    graph = _base_graph(case_id, revision=7)
    graph["readHistory"] = [
        {"nodeId": "11111111-1111-1111-1111-111111111111", "at": "2026-01-01T00:00:00Z"}
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        if (method, path) == ("GET", f"/backend/graph/{case_id}"):
            return httpx.Response(200, json=graph)
        raise AssertionError(f"an already-read question should not be re-saved: {method} {path}")

    client = _client(tmp_path, handler)
    args = argparse.Namespace(case=case_id, number="1", no_mark_read=False, json=True)

    commands.cmd_read(client, args)


def test_decline_writes_reason_onto_declined_questions(tmp_path: Path):
    case_id = "case-2"
    graph = _base_graph(case_id, revision=1)
    graph["suggestions"] = [
        {
            "id": "sugg-1",
            "parentNodeId": "11111111-1111-1111-1111-111111111111",
            "text": "A follow-up?",
            "bigPicture": False,
            "createdAt": "2026-01-01T00:00:00Z",
        }
    ]
    saved_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        if (method, path) == ("GET", f"/backend/graph/{case_id}"):
            return httpx.Response(200, json=graph)
        if (method, path) == ("PUT", f"/backend/graph/{case_id}"):
            import json as _json

            body = _json.loads(request.content)
            saved_bodies.append(body)
            return httpx.Response(200, json={"id": case_id, "revision": 2})
        raise AssertionError(f"unexpected request: {method} {path}")

    client = _client(tmp_path, handler)
    args = argparse.Namespace(case=case_id, suggestion_id="sugg-1", reason="too_basic", json=True)

    commands.cmd_decline(client, args)

    assert len(saved_bodies) == 1
    body = saved_bodies[0]
    assert body["suggestions"] == []
    declined = body["declinedQuestions"]
    assert len(declined) == 1
    assert declined[0]["text"] == "A follow-up?"
    assert declined[0]["reason"] == "too_basic"
    assert declined[0]["parentNodeId"] == "11111111-1111-1111-1111-111111111111"
    assert "declinedAt" in declined[0]


def test_decline_unknown_suggestion_raises_clear_error(tmp_path: Path):
    from talleyrand.cli.errors import TallyError

    case_id = "case-3"
    graph = _base_graph(case_id, revision=1)

    def handler(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == ("GET", f"/backend/graph/{case_id}"):
            return httpx.Response(200, json=graph)
        raise AssertionError("no save should be attempted")

    client = _client(tmp_path, handler)
    args = argparse.Namespace(case=case_id, suggestion_id="missing", reason=None, json=True)

    with pytest.raises(TallyError):
        commands.cmd_decline(client, args)
