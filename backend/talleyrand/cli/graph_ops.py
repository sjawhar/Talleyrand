"""
Graph mutations shared by the commands that write to a case: `ask`, `suggest`,
`accept`, `decline`, and `doc add`. Each mutation operates on the raw JSON
dict returned by GET /graph/{id} (camelCase keys, exactly what PUT expects
back) rather than round-tripping through pydantic models, since the server is
the source of truth for validation; the CLI only needs to read outline
numbers and IDs out of it, which outline.py handles by parsing a throwaway
GraphNoId view.

`mutate_and_save` is the save-with-retry every write command goes through:
read, mutate in place, save at the read revision; on a 409 (someone else
saved first), re-read once, re-apply the same mutation, and save again. The
mutation callables all key off IDs generated once by the caller, so replaying
one against a freshly re-read graph is safe.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from talleyrand.cli.client import RevisionConflict, TallyClient
from talleyrand.cli.errors import TallyError
from talleyrand.cli.outline import build_outline, node_id_for_outline, parse_graph
from talleyrand.core.model_settings import MODEL_CONFIGS, RETIRED_MODELS, resolve_model_id

# The model a question starts on when its case has no earlier question to
# inherit one from; like an inherited model, it then moves to a provider the
# server can run (usable_model).
DEFAULT_MODEL = "claude-fable-5-1-high"


def _ladders() -> dict[str, list[str]]:
    """Each provider's presets from fastest to deepest reasoning, the order the
    browser's model picker and its sameTierModel use; providers in the order
    their presets are listed, so the first usable one is the browser's too."""
    ladders: dict[str, list[str]] = {}
    for config in sorted(MODEL_CONFIGS.values(), key=lambda config: config.order):
        ladders.setdefault(config.provider, []).append(config.id)
    return ladders


_LADDERS = _ladders()


def usable_model(model_id: str, providers: dict[str, bool] | None) -> str:
    """
    The model a new question can run on, given which providers the server
    holds credentials for (GET /config's `providers`; None when the server
    publishes none, and then its own error on the job speaks for it).

    Mirrors the browser's resolveUsableModel for a caller that holds no keys:
    a retired id becomes its replacement, and a model whose provider the
    server cannot run moves to the same thinking position on the first
    provider it can (GPT-6 Astra high → Claude Fable 5.1 high). With no
    usable provider at all, nothing could answer, so this refuses.
    """
    try:
        current = resolve_model_id(model_id)
    except ValueError as exc:
        raise TallyError(str(exc)) from exc
    if providers is None:
        return current
    provider = MODEL_CONFIGS[current].provider
    if providers.get(provider):
        return current
    usable = next((p for p in _LADDERS if providers.get(p)), None)
    if usable is None:
        raise TallyError(
            "The server holds no model credentials of its own, and `tally` sends no keys, "
            f"so '{current}' cannot answer here, and neither can any other model."
        )
    position = _LADDERS[provider].index(current)
    ladder = _LADDERS[usable]
    return ladder[min(position, len(ladder) - 1)]


def default_model(graph_dict: dict[str, Any], providers: dict[str, bool] | None) -> str:
    """The model a new question inherits: the case's most recently created
    question's selected model (DEFAULT_MODEL for a case with none), made
    usable on this server (usable_model).

    An inherited id this version does not know (say, from a question asked
    with a mistyped --model) gives way to DEFAULT_MODEL, as the browser's
    resolveModelId does, with a note on stderr: refusing would leave every
    later `ask` and `accept` on the case stuck behind one bad question."""
    inherited = DEFAULT_MODEL
    nodes = graph_dict.get("nodes", [])
    if nodes:
        last_node_id = nodes[-1]["id"]
        for content in graph_dict.get("nodeContents", []):
            if content["id"] == last_node_id:
                inherited = content.get("selectedModel", DEFAULT_MODEL)
    if inherited not in MODEL_CONFIGS and inherited not in RETIRED_MODELS:
        print(
            f"note: the case's last question uses unknown model '{inherited}'; "
            f"starting from {DEFAULT_MODEL} instead.",
            file=sys.stderr,
        )
        inherited = DEFAULT_MODEL
    return usable_model(inherited, providers)


def find_node(graph_dict: dict[str, Any], outline: str) -> str:
    """The node id for an outline number (e.g. "1.2"); raises if unknown."""
    graph = parse_graph(graph_dict)
    tree, _ = build_outline(graph)
    node_id = node_id_for_outline(tree, outline)
    if node_id is None:
        raise TallyError(f"No question numbered [{outline}] in this case.")
    return node_id


def resolve_parent(graph_dict: dict[str, Any], outline: str | None) -> str | None:
    """Node id an --under outline number names, or None for a new root question."""
    return None if outline is None else find_node(graph_dict, outline)


def outline_for_node(graph_dict: dict[str, Any], node_id: str) -> str:
    graph = parse_graph(graph_dict)
    tree, _ = build_outline(graph)
    return tree.outline.get(node_id, "?")


def add_question(
    graph_dict: dict[str, Any],
    *,
    node_id: str,
    parent_id: str | None,
    edge_id: str,
    text: str,
    model: str,
) -> None:
    """Append a new question node/content, and its parent edge if it is a
    follow-up, mirroring the frontend's createResearchChildNode."""
    graph_dict.setdefault("nodes", []).append({"id": node_id})
    graph_dict.setdefault("nodeContents", []).append(
        {
            "id": node_id,
            "query": text,
            "response": "",
            "selectedModel": model,
            "documents": [],
            "selectionSuggestions": [],
            "selections": [],
        }
    )
    if parent_id is not None:
        graph_dict.setdefault("edges", []).append(
            {"id": edge_id, "source": parent_id, "target": node_id}
        )


def accept_suggestion(
    graph_dict: dict[str, Any], suggestion_id: str, *, node_id: str, edge_id: str, model: str
) -> str:
    """Mirrors the frontend's acceptSuggestion: file the suggestion's text as
    a question under its parent, and drop the suggestion. Returns the parent
    node id (the id `--under` for it would have been)."""
    suggestions = graph_dict.get("suggestions", [])
    suggestion = next((s for s in suggestions if s["id"] == suggestion_id), None)
    if suggestion is None:
        raise TallyError(f"No pending suggestion with id {suggestion_id}.")
    graph_dict["suggestions"] = [s for s in suggestions if s["id"] != suggestion_id]
    add_question(
        graph_dict,
        node_id=node_id,
        parent_id=suggestion["parentNodeId"],
        edge_id=edge_id,
        text=suggestion["text"],
        model=model,
    )
    return suggestion["parentNodeId"]


def decline_suggestion(graph_dict: dict[str, Any], suggestion_id: str, reason: str | None) -> None:
    """Mirrors the frontend's declineSuggestion: move it to declinedQuestions
    with the given reason (None = declined without one)."""
    suggestions = graph_dict.get("suggestions", [])
    suggestion = next((s for s in suggestions if s["id"] == suggestion_id), None)
    if suggestion is None:
        raise TallyError(f"No pending suggestion with id {suggestion_id}.")
    graph_dict["suggestions"] = [s for s in suggestions if s["id"] != suggestion_id]
    graph_dict.setdefault("declinedQuestions", []).append(
        {
            "text": suggestion["text"],
            "reason": reason,
            "parentNodeId": suggestion["parentNodeId"],
            "declinedAt": datetime.now(UTC).isoformat(),
        }
    )


def add_document(
    graph_dict: dict[str, Any], *, doc_id: str, name: str, content: str, node_id: str | None
) -> None:
    """Attach a text document to the case (node_id=None) or to a question."""
    document = {"id": doc_id, "name": name, "type": "txt", "content": content}
    if node_id is None:
        graph_dict.setdefault("caseDocuments", []).append(document)
        return
    for node_content in graph_dict.get("nodeContents", []):
        if node_content["id"] == node_id:
            node_content.setdefault("documents", []).append(document)
            return
    raise TallyError(f"Question node {node_id} not found in this case.")


def add_read_event(graph_dict: dict[str, Any], node_id: str) -> None:
    """Mirrors the frontend's markRead (researchStore.ts): a node enters
    readHistory at most once — the first-read choke point. Already read is a
    silent no-op, not a second event, so this is safe to call on every `read`
    and every `ask` that waited for its answer."""
    if any(event["nodeId"] == node_id for event in graph_dict.get("readHistory", [])):
        return
    graph_dict.setdefault("readHistory", []).append(
        {"nodeId": node_id, "at": datetime.now(UTC).isoformat()}
    )


def is_read(graph_dict: dict[str, Any], node_id: str) -> bool:
    """Whether node_id already has a read event, so a caller can skip the
    save entirely rather than round-trip a PUT that add_read_event would
    turn into a no-op."""
    return any(event["nodeId"] == node_id for event in graph_dict.get("readHistory", []))


def mutate_and_save(
    client: TallyClient,
    case_id: str,
    mutate: Callable[[dict[str, Any]], None],
    *,
    initial_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read (unless `initial_graph` was already read by the caller), mutate,
    save at that revision; on a 409 re-read once, re-apply, save again."""
    graph = initial_graph if initial_graph is not None else client.get_graph(case_id)
    mutate(graph)
    try:
        revision = client.save_graph(case_id, graph)
    except RevisionConflict:
        graph = client.get_graph(case_id)
        mutate(graph)
        try:
            revision = client.save_graph(case_id, graph)
        except RevisionConflict as exc:
            raise TallyError(
                "This case keeps changing elsewhere; the save was retried once and still "
                "conflicted. Try again."
            ) from exc
    graph["revision"] = revision
    return graph


def wait_for_job(
    client: TallyClient,
    case_id: str,
    job: dict[str, Any],
    *,
    waiting_for: str,
    timeout: float,
    poll_interval: float = 1.0,
) -> None:
    """
    Wait for a job the caller started (`job` is the start call's record) to
    end, by polling its record (GET /research/{case}/jobs/{id}). Returns once
    it is done, or once its record has retired: the server retires a record
    when a save proves the case took in its result, so either way the result
    is now in the case. A failed job raises its own error at once.
    """
    deadline = time.monotonic() + timeout
    record: dict[str, Any] | None = job
    while record is not None:
        if record["status"] == "error":
            raise TallyError(record.get("error") or f"Generating {waiting_for} failed.")
        if record["status"] == "done":
            return
        if time.monotonic() > deadline:
            raise TallyError(f"Timed out after {timeout:.0f}s waiting for {waiting_for}.")
        time.sleep(poll_interval)
        record = client.get_job(case_id, job["id"])


def wait_for_answer(
    client: TallyClient,
    case_id: str,
    node_id: str,
    job: dict[str, Any],
    *,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """
    Wait for a started answer job to end, then read its answer from the case:
    every graph read folds a finished answer into the node's content
    (features.research.generation.overlay). Re-posting /generate to learn the
    outcome would be wrong: the server treats an errored job as retryable and
    would start a fresh one instead of reporting the failure.

    Returns the graph as read (so the caller can save a follow-up mutation,
    e.g. a read event, without another GET at a stale revision).
    """
    wait_for_job(client, case_id, job, waiting_for="the answer", timeout=timeout)
    graph = client.get_graph(case_id)
    content = next((c for c in graph.get("nodeContents", []) if c["id"] == node_id), None)
    if content is None or not content.get("response"):
        raise TallyError(
            "The answer job ended, but the case holds no answer for this question: "
            "it was removed, or its generation was restarted elsewhere."
        )
    return graph


def wait_for_suggestions(
    client: TallyClient,
    case_id: str,
    node_id: str,
    job: dict[str, Any],
    *,
    before_ids: set[str],
    timeout: float = 300.0,
) -> list[dict[str, Any]]:
    """Wait for a started suggestion job to end, then return the suggestions
    under node_id the case has gained (none, when the job found nothing new):
    every graph read folds a finished job's suggestions in
    (features.research.generation.overlay)."""
    wait_for_job(client, case_id, job, waiting_for="suggestions", timeout=timeout)
    graph = client.get_graph(case_id)
    return [
        s
        for s in graph.get("suggestions", [])
        if s["parentNodeId"] == node_id and s["id"] not in before_ids
    ]
