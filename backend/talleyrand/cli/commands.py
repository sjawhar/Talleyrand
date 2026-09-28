"""
Subcommand handlers for `tally`. Each takes the parsed args and an already
constructed TallyClient, and prints its result — human-readable text by
default, or one JSON value with --json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from talleyrand.cli.client import TallyClient
from talleyrand.cli.errors import TallyError
from talleyrand.cli.graph_ops import (
    accept_suggestion,
    add_document,
    add_question,
    add_read_event,
    decline_suggestion,
    default_model,
    find_node,
    is_read,
    mutate_and_save,
    outline_for_node,
    resolve_parent,
    wait_for_answer,
    wait_for_suggestions,
)
from talleyrand.cli.outline import (
    build_outline,
    node_id_for_outline,
    outline_to_json,
    parse_graph,
    render_outline_text,
)


def emit(as_json: bool, data: Any, text: str) -> None:
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        print(text)


def cmd_login(client: TallyClient, args: Any) -> None:
    me = client.login()
    who = me.get("name") or me.get("email") or me.get("id")
    emit(args.json, me, f"Signed in as {who} ({me.get('email')})")


def cmd_cases(client: TallyClient, args: Any) -> None:
    cases = client.list_cases()
    text = "\n".join(f"{case['id']}  {case['name']}" for case in cases) or "No cases."
    emit(args.json, cases, text)


def cmd_import(client: TallyClient, args: Any) -> None:
    payload = json.loads(Path(args.file).read_text())
    case_id = client.import_case(payload)
    emit(args.json, {"id": case_id}, case_id)


def cmd_export(client: TallyClient, args: Any) -> None:
    graph = client.get_graph(args.case)
    print(json.dumps(graph, indent=None if args.json else 2))


def cmd_outline(client: TallyClient, args: Any) -> None:
    graph_dict = client.get_graph(args.case)
    graph = parse_graph(graph_dict)
    _, entries = build_outline(graph)
    if args.json:
        print(json.dumps(outline_to_json(entries), indent=2))
    else:
        print(render_outline_text(entries) or "No questions in this case.")


def cmd_read(client: TallyClient, args: Any) -> None:
    graph_dict = client.get_graph(args.case)
    graph = parse_graph(graph_dict)
    tree, _ = build_outline(graph)
    node_id = node_id_for_outline(tree, args.number)
    if node_id is None:
        raise TallyError(f"No question numbered [{args.number}] in this case.")
    content = next(c for c in graph.node_contents if str(c.id) == node_id)

    if content.response and not args.no_mark_read and not is_read(graph_dict, node_id):

        def mutate(g: dict[str, Any]) -> None:
            add_read_event(g, node_id)

        mutate_and_save(client, args.case, mutate, initial_graph=graph_dict)

    data = {
        "outline": args.number,
        "id": node_id,
        "query": content.query,
        "answer": content.response,
        "highlights": [h.text for h in content.highlights],
        "sources": [{"url": s.url, "title": s.title} for s in content.sources],
    }
    if args.json:
        print(json.dumps(data, indent=2))
        return
    lines = [f"[{args.number}] {content.query}", "", content.response or "(not yet answered)"]
    if content.highlights:
        lines.append("")
        lines.append("Highlights:")
        lines.extend(f"  - {h.text}" for h in content.highlights)
    if content.sources:
        lines.append("")
        lines.append("Sources:")
        lines.extend(f"  - {s.title or s.url} <{s.url}>" for s in content.sources)
    print("\n".join(lines))


def cmd_ask(client: TallyClient, args: Any) -> None:
    graph_dict = client.get_graph(args.case)
    parent_id = resolve_parent(graph_dict, args.under)
    model = args.model or default_model(graph_dict, client.server_providers())
    node_id, edge_id = str(uuid4()), str(uuid4())

    def mutate(graph: dict[str, Any]) -> None:
        add_question(
            graph,
            node_id=node_id,
            parent_id=parent_id,
            edge_id=edge_id,
            text=args.text,
            model=model,
        )

    mutate_and_save(client, args.case, mutate, initial_graph=graph_dict)
    job = client.generate_answer(args.case, node_id)

    if args.no_wait:
        emit(args.json, {"id": node_id, "status": job["status"]}, f"[{node_id}] {job['status']}")
        return

    answered_graph = wait_for_answer(client, args.case, node_id, job, timeout=args.timeout)
    content = next(c for c in answered_graph.get("nodeContents", []) if c["id"] == node_id)

    if not args.no_mark_read and not is_read(answered_graph, node_id):

        def mark_read_mutate(g: dict[str, Any]) -> None:
            add_read_event(g, node_id)

        mutate_and_save(client, args.case, mark_read_mutate, initial_graph=answered_graph)

    data = {
        "id": node_id,
        "answer": content.get("response") or "",
        "sources": content.get("sources", []),
    }
    emit(args.json, data, data["answer"])


def cmd_suggest(client: TallyClient, args: Any) -> None:
    graph_dict = client.get_graph(args.case)
    node_id = find_node(graph_dict, args.number)
    before_ids = {
        s["id"] for s in graph_dict.get("suggestions", []) if s["parentNodeId"] == node_id
    }
    job = client.suggest_followups(args.case, node_id, request_more=args.more)
    fresh = wait_for_suggestions(
        client, args.case, node_id, job, before_ids=before_ids, timeout=args.timeout
    )
    text = "\n".join(f"{s['id']}  {s['text']}" for s in fresh) or "No new suggestions."
    emit(args.json, fresh, text)


def cmd_accept(client: TallyClient, args: Any) -> None:
    graph_dict = client.get_graph(args.case)
    node_id, edge_id = str(uuid4()), str(uuid4())
    model = default_model(graph_dict, client.server_providers())

    def mutate(graph: dict[str, Any]) -> None:
        accept_suggestion(graph, args.suggestion_id, node_id=node_id, edge_id=edge_id, model=model)

    saved = mutate_and_save(client, args.case, mutate, initial_graph=graph_dict)
    outline = outline_for_node(saved, node_id)
    emit(args.json, {"id": node_id, "outline": outline}, f"Accepted as [{outline}]")


def cmd_decline(client: TallyClient, args: Any) -> None:
    def mutate(graph: dict[str, Any]) -> None:
        decline_suggestion(graph, args.suggestion_id, args.reason)

    mutate_and_save(client, args.case, mutate)
    reason_text = args.reason or "no reason given"
    emit(
        args.json,
        {"id": args.suggestion_id, "reason": args.reason},
        f"Declined {args.suggestion_id} ({reason_text})",
    )


def cmd_doc_add(client: TallyClient, args: Any) -> None:
    path = Path(args.file)
    if path.suffix.lower() not in (".txt", ".md"):
        raise TallyError("Only .txt and .md files can be attached as text documents.")
    content = path.read_text()
    name = args.name or path.name
    doc_id = str(uuid4())

    graph_dict = client.get_graph(args.case)
    node_id = resolve_parent(graph_dict, args.under)

    def mutate(graph: dict[str, Any]) -> None:
        add_document(graph, doc_id=doc_id, name=name, content=content, node_id=node_id)

    mutate_and_save(client, args.case, mutate, initial_graph=graph_dict)
    emit(args.json, {"id": doc_id, "name": name}, f"Attached {name} ({doc_id})")


def cmd_report(client: TallyClient, args: Any) -> None:
    graph_dict = client.get_graph(args.case)
    markdown = client.report(graph_dict, guidance=args.guidance or "")
    if args.json:
        print(json.dumps({"markdown": markdown}))
    else:
        print(markdown)
