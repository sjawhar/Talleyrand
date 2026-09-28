"""
The CLI's outline numbers are the app's: roots numbered in creation order,
follow-ups numbered under their parent, whatever order the nodes were
created in. `find_node` maps a number back to its question and names an
unknown one.
"""

import pytest

from talleyrand.cli.errors import TallyError
from talleyrand.cli.graph_ops import find_node
from talleyrand.cli.outline import build_outline, entry_markers, parse_graph

FIRST = "11111111-1111-1111-1111-111111111111"
SECOND = "22222222-2222-2222-2222-222222222222"
FOLLOW_UP = "33333333-3333-3333-3333-333333333333"


def _content(node_id: str, query: str, response: str) -> dict:
    return {
        "id": node_id,
        "query": query,
        "response": response,
        "selectedModel": "claude-sonnet-5",
        "documents": [],
        "selectionSuggestions": [],
        "selections": [],
    }


def _case() -> dict:
    # The follow-up under the first root was created after the second root,
    # so creation order alone would number it [3].
    return {
        "nodes": [{"id": FIRST}, {"id": SECOND}, {"id": FOLLOW_UP}],
        "edges": [
            {"id": "44444444-4444-4444-4444-444444444444", "source": FIRST, "target": FOLLOW_UP}
        ],
        "nodeContents": [
            _content(FIRST, "First?", "Yes."),
            _content(SECOND, "Second?", "No."),
            _content(FOLLOW_UP, "Why yes?", ""),
        ],
        "brief": "",
    }


def test_outline_numbers_follow_ups_under_their_parent():
    _, entries = build_outline(parse_graph(_case()))

    assert [(entry.outline, entry.node_id) for entry in entries] == [
        ("1", FIRST),
        ("1.1", FOLLOW_UP),
        ("2", SECOND),
    ]
    assert [entry_markers(entry.content) for entry in entries] == [["answered"], [], ["answered"]]


def test_find_node_maps_a_number_to_its_question():
    assert find_node(_case(), "1.1") == FOLLOW_UP
    assert find_node(_case(), "2") == SECOND


def test_find_node_names_an_unknown_number():
    with pytest.raises(TallyError, match=r"No question numbered \[1\.2\] in this case\."):
        find_node(_case(), "1.2")
