"""
`suggest` and `ask` must stop waiting as soon as the job they started ends,
whatever it produced: a suggestion job that finishes with no new suggestions,
or a job that fails, is a result to report now, not something to discover
only when --timeout runs out.

The server's job record is reachable over HTTP at a GET whose last path
segment is the job id the start call returned; these tests serve it at any
such path so they pin the command's behaviour, not a route name. Each command
runs on a worker thread with a generous --timeout, and the test fails if it is
still waiting well before that timeout would end it.
"""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

from talleyrand.cli import commands
from talleyrand.cli.client import TallyClient
from talleyrand.cli.errors import TallyError
from talleyrand.cli.tokens import Tokens, save_tokens

CASE_ID = "case-outcome"
ROOT_ID = "11111111-1111-1111-1111-111111111111"
JOB_ID = "job-7f3a"

# Far longer than the test will wait: a command still running when the test
# gives up is waiting for its timeout, which is the defect under test.
COMMAND_TIMEOUT = 600.0
TEST_WAIT = 20.0


def _client(tmp_path: Path, handler) -> TallyClient:
    tokens_file = tmp_path / "tokens.json"
    save_tokens(Tokens(access_token="access", refresh_token="refresh"), tokens_file)
    return TallyClient(tokens_file=str(tokens_file), transport=httpx.MockTransport(handler))


def _graph(extra_nodes: list[str] | None = None, revision: int = 3) -> dict[str, Any]:
    node_ids = [ROOT_ID, *(extra_nodes or [])]
    return {
        "id": CASE_ID,
        "name": "A case",
        "revision": revision,
        "nodes": [{"id": node_id} for node_id in node_ids],
        "edges": [],
        "nodeContents": [
            {
                "id": node_id,
                "query": "Root question?" if node_id == ROOT_ID else "New question?",
                # Only the root is answered; a failed answer job leaves the new
                # question's response empty.
                "response": "Root answer." if node_id == ROOT_ID else "",
                "selectedModel": "claude-fable-5-1-high",
                "documents": [],
                "selectionSuggestions": [],
                "selections": [],
            }
            for node_id in node_ids
        ],
        "brief": "",
        "caseDocuments": [],
        "suggestions": [],
        "declinedQuestions": [],
        "readHistory": [],
    }


def _job(kind: str, node_id: str, status: str, error: str | None = None) -> dict[str, Any]:
    return {
        "id": JOB_ID,
        "kind": kind,
        "nodeId": node_id,
        "status": status,
        "answer": None,
        "answeredAt": None,
        "sources": [],
        "sourcesFound": 0,
        "suggestions": [],
        "error": error,
    }


def _is_job_lookup(request: httpx.Request) -> bool:
    return request.method == "GET" and request.url.path.rstrip("/").split("/")[-1] == JOB_ID


def _run_bounded(fn) -> tuple[bool, BaseException | None]:
    """Run fn on a daemon thread; return (finished within TEST_WAIT, exception)."""
    outcome: dict[str, BaseException | None] = {"error": None}

    def target() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - reported to the test thread
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(TEST_WAIT)
    return (not worker.is_alive(), outcome["error"])


def _suggest_handler(final_job: dict[str, Any], starts: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        if (method, path) == ("GET", f"/backend/graph/{CASE_ID}"):
            # The job never adds a suggestion, so the case never changes.
            return httpx.Response(200, json=_graph())
        if method == "POST" and path.endswith("/suggest-followups"):
            starts.append(path)
            return httpx.Response(200, json=_job("suggestions", ROOT_ID, "running"))
        if _is_job_lookup(request):
            return httpx.Response(200, json=final_job)
        return httpx.Response(404, json={"detail": "Not found"})

    return handler


def _suggest_args() -> argparse.Namespace:
    return argparse.Namespace(
        case=CASE_ID, number="1", more=False, timeout=COMMAND_TIMEOUT, json=False
    )


def test_suggest_reports_no_new_suggestions_as_soon_as_the_job_finishes_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    starts: list[str] = []
    finished_job = _job("suggestions", ROOT_ID, "done")  # done, suggestions []
    client = _client(tmp_path, _suggest_handler(finished_job, starts))

    finished, error = _run_bounded(lambda: commands.cmd_suggest(client, _suggest_args()))

    assert finished, (
        f"`suggest` was still waiting {TEST_WAIT:.0f}s after its job finished with no "
        "new suggestions; it should report that at once instead of waiting for --timeout"
    )
    assert error is None, f"an empty result is not a failure, but `suggest` raised {error!r}"
    out = capsys.readouterr().out.lower()
    assert "no" in out and "suggestion" in out, f"expected a no-suggestions message, got {out!r}"
    assert len(starts) == 1, "each suggest call starts a fresh job; it must start exactly one"


def test_suggest_fails_with_the_job_error_as_soon_as_the_job_fails(tmp_path: Path):
    starts: list[str] = []
    message = "Suggestion generation failed: the model was unavailable"
    failed_job = _job("suggestions", ROOT_ID, "error", error=message)
    client = _client(tmp_path, _suggest_handler(failed_job, starts))

    finished, error = _run_bounded(lambda: commands.cmd_suggest(client, _suggest_args()))

    assert finished, (
        f"`suggest` was still waiting {TEST_WAIT:.0f}s after its job failed; it should "
        "fail at once with the job's error instead of waiting for --timeout"
    )
    assert isinstance(error, TallyError), f"expected a TallyError, got {error!r}"
    assert message in str(error), f"the job's error must reach the user, got {str(error)!r}"
    assert len(starts) == 1


def test_ask_fails_with_the_generation_error_as_soon_as_the_answer_job_fails(tmp_path: Path):
    message = "Unknown model 'claude-sonnet-5-medium'. Current models: claude-sonnet-5"
    state: dict[str, Any] = {"generate_posts": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        if (method, path) == ("GET", f"/backend/graph/{CASE_ID}"):
            extra = [state["node_id"]] if "node_id" in state else []
            return httpx.Response(200, json=_graph(extra, revision=4 if extra else 3))
        if (method, path) == ("PUT", f"/backend/graph/{CASE_ID}"):
            body = json.loads(request.content)
            state["node_id"] = next(n["id"] for n in body["nodes"] if n["id"] != ROOT_ID)
            return httpx.Response(200, json={"id": CASE_ID, "revision": 4})
        if method == "POST" and path.endswith("/generate"):
            # A second start would replace the errored record with a fresh job
            # (GenerationJobManager.start_answer_job), hiding the failure.
            state["generate_posts"] += 1
            return httpx.Response(200, json=_job("answer", state["node_id"], "running"))
        if _is_job_lookup(request):
            return httpx.Response(200, json=_job("answer", state["node_id"], "error", message))
        return httpx.Response(404, json={"detail": "Not found"})

    client = _client(tmp_path, handler)
    args = argparse.Namespace(
        case=CASE_ID,
        text="New question?",
        under="1",
        model=None,
        no_wait=False,
        no_mark_read=False,
        timeout=COMMAND_TIMEOUT,
        json=False,
    )

    finished, error = _run_bounded(lambda: commands.cmd_ask(client, args))

    assert finished, (
        f"`ask` was still waiting {TEST_WAIT:.0f}s after its answer job failed; it should "
        "fail at once with the generation error instead of waiting for --timeout"
    )
    assert isinstance(error, TallyError), f"expected a TallyError, got {error!r}"
    assert message in str(error), f"the generation error must reach the user, got {str(error)!r}"
    assert state["generate_posts"] == 1, "ask must start generation exactly once"
