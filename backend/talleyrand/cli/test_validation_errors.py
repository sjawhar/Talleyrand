"""
A request the server rejects as malformed (422) must come back as a short
error naming what is wrong and where. FastAPI's 422 body echoes the offending
input, so printing it verbatim buries the answer under the whole payload: an
import with one missing field printed all 16 MB of the case to the terminal.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from talleyrand.cli.client import TallyClient
from talleyrand.cli.errors import TallyError
from talleyrand.cli.tokens import Tokens, save_tokens

BIG_CONTENT = "x" * 1_000_000


def test_a_422_names_the_field_and_problem_without_echoing_the_input(tmp_path: Path):
    tokens_file = tmp_path / "tokens.json"
    save_tokens(Tokens(access_token="access", refresh_token="refresh"), tokens_file)

    def handler(request: httpx.Request) -> httpx.Response:
        # The shape FastAPI answers with for a document missing its "type".
        return httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "type": "missing",
                        "loc": ["body", "caseDocuments", 0, "type"],
                        "msg": "Field required",
                        "input": {"id": "d1", "name": "big.txt", "content": BIG_CONTENT},
                    }
                ]
            },
        )

    client = TallyClient(tokens_file=str(tokens_file), transport=httpx.MockTransport(handler))

    with pytest.raises(TallyError) as caught:
        client.import_case({"name": "c", "caseDocuments": [{"content": BIG_CONTENT}]})

    message = str(caught.value)
    assert "caseDocuments" in message and "type" in message, message[:500]
    assert "Field required" in message, message[:500]
    assert len(message) < 1_000, f"the error is {len(message):,} characters long"
