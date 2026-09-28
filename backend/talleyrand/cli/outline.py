"""
Outline numbering and rendering for `tally outline` / `tally read`.

Reuses the backend's own talleyrand.features.research.context_builder so the
CLI's numbers always match the app's. Deliberately imported directly (not via
talleyrand.features.research.router or talleyrand.main) since this module,
and its dtos.py dependency, never touch talleyrand.core.config — unlike the
router, which pulls in auth_jwt and therefore the server's Settings(). That
keeps `tally` runnable with no server environment variables set at all.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from talleyrand.features.graph.dtos import GraphNoId, NodeContentDTO, ResearchSuggestionDTO
from talleyrand.features.research.context_builder import QuestionTree, build_question_tree


@dataclass
class OutlineEntry:
    outline: str
    node_id: str
    content: NodeContentDTO
    suggestions: list[ResearchSuggestionDTO]


def parse_graph(graph_dict: dict[str, Any]) -> GraphNoId:
    """Parse a /graph/{id} (or /graph/import) JSON body into the DTO the
    backend's own tree builder and prompt code expect."""
    return GraphNoId(**graph_dict)


def build_outline(graph: GraphNoId) -> tuple[QuestionTree, list[OutlineEntry]]:
    tree = build_question_tree(graph)
    content_by_id = {str(content.id): content for content in graph.node_contents}
    suggestions_by_parent: dict[str, list[ResearchSuggestionDTO]] = defaultdict(list)
    for suggestion in graph.suggestions:
        suggestions_by_parent[str(suggestion.parent_node_id)].append(suggestion)

    entries: list[OutlineEntry] = []
    for node_id in tree.dfs_order:
        content = content_by_id.get(node_id)
        if content is None:
            continue
        entries.append(
            OutlineEntry(
                outline=tree.outline[node_id],
                node_id=node_id,
                content=content,
                suggestions=suggestions_by_parent.get(node_id, []),
            )
        )
    return tree, entries


def node_id_for_outline(tree: QuestionTree, outline: str) -> str | None:
    for node_id, number in tree.outline.items():
        if number == outline:
            return node_id
    return None


def entry_markers(content: NodeContentDTO) -> list[str]:
    markers = []
    if content.response:
        markers.append("answered")
    if content.resolved_at is not None:
        markers.append("resolved")
    if content.loved_at is not None:
        markers.append("loved")
    return markers


def render_outline_text(entries: list[OutlineEntry]) -> str:
    lines: list[str] = []
    for entry in entries:
        markers = entry_markers(entry.content)
        suffix = f" ({', '.join(markers)})" if markers else ""
        lines.append(f"[{entry.outline}] {entry.content.query}{suffix}")
        for suggestion in entry.suggestions:
            tag = " [big-picture]" if suggestion.big_picture else ""
            lines.append(f"      suggestion {suggestion.id}{tag}: {suggestion.text}")
    return "\n".join(lines)


def outline_to_json(entries: list[OutlineEntry]) -> list[dict[str, Any]]:
    result = []
    for entry in entries:
        result.append(
            {
                "outline": entry.outline,
                "id": entry.node_id,
                "query": entry.content.query,
                "markers": entry_markers(entry.content),
                "suggestions": [
                    {
                        "id": suggestion.id,
                        "text": suggestion.text,
                        "bigPicture": suggestion.big_picture,
                    }
                    for suggestion in entry.suggestions
                ],
            }
        )
    return result
