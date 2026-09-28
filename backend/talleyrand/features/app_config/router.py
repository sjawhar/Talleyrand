"""
Public, unauthenticated runtime configuration the frontend needs before sign-in:
whether this instance runs single-user local mode, which model providers the
server holds its own credentials for, which provider serves background
features, and whether dictation is available.
"""

from typing import Literal

from fastapi import APIRouter

from talleyrand.core.config import settings
from talleyrand.core.server_credentials import server_provider_available
from talleyrand.features.graph.dtos import BaseSchema

router = APIRouter(tags=["app_config"])


class ProvidersAvailability(BaseSchema):
    openai: bool
    anthropic: bool


class AppConfigResponse(BaseSchema):
    local_login: bool
    local_user_email: str | None
    providers: ProvidersAvailability
    background_provider: Literal["openai", "anthropic"]
    dictation: bool


@router.get("/config", response_model=AppConfigResponse)
async def get_app_config() -> AppConfigResponse:
    """Runtime configuration the frontend reads before offering sign-in options."""
    return AppConfigResponse(
        local_login=settings.local_user_email is not None,
        local_user_email=settings.local_user_email,
        providers=ProvidersAvailability(
            openai=server_provider_available("openai"),
            anthropic=server_provider_available("anthropic"),
        ),
        background_provider=settings.background_provider,
        dictation=settings.dictation_enabled,
    )
