"""
Dependencies for graph feature endpoints.
"""

from typing import Annotated

from fastapi import Header, HTTPException

from talleyrand.core.config import settings
from talleyrand.core.server_credentials import server_provider_available

# The browser client renders a 400's detail verbatim as the error text, so this
# must read as prose and point at the fix (the Settings modal) — never at the
# HTTP header the client sets on its own.
MISSING_OPENAI_KEY_DETAIL = "No OpenAI API key configured. Please add one in Settings to continue."
MISSING_ANTHROPIC_KEY_DETAIL = (
    "No Anthropic API key configured. Please add one in Settings to continue."
)


def get_openai_api_key(
    x_openai_api_key: Annotated[str | None, Header()] = None,
) -> str:
    """Extract OpenAI API key from request header."""
    if not x_openai_api_key:
        raise HTTPException(status_code=400, detail=MISSING_OPENAI_KEY_DETAIL)
    return x_openai_api_key


def get_background_api_key(
    x_openai_api_key: Annotated[str | None, Header()] = None,
    x_anthropic_api_key: Annotated[str | None, Header()] = None,
) -> str:
    """
    The API key backing the background features (kickstart, suggestions,
    reports, cheat sheets, naming): the header for whichever provider
    settings.background_provider selects.

    Returns "" when the server holds its own credentials for that provider —
    parse_structured and count_prompt_tokens fall back to them instead of
    failing for lack of a header. There is no server-held OpenAI credential,
    so a missing OpenAI key always raises.
    """
    if settings.background_provider == "openai":
        if not x_openai_api_key:
            raise HTTPException(status_code=400, detail=MISSING_OPENAI_KEY_DETAIL)
        return x_openai_api_key
    if x_anthropic_api_key:
        return x_anthropic_api_key
    if server_provider_available("anthropic"):
        return ""
    raise HTTPException(status_code=400, detail=MISSING_ANTHROPIC_KEY_DETAIL)
