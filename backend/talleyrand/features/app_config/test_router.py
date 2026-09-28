"""
GET /config is the frontend's one call to learn, before any sign-in, whether
this instance offers local login, which model providers the server holds
credentials for, which provider drives background features, and whether
dictation is on.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from talleyrand.core import server_credentials
from talleyrand.core.config import Settings

from . import router as router_module
from .router import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_config_reflects_default_settings():
    response = client.get("/config")
    assert response.status_code == 200
    body = response.json()
    assert body["localLogin"] is False
    assert body["localUserEmail"] is None
    assert body["providers"] == {"openai": False, "anthropic": False}
    assert body["backgroundProvider"] == "openai"
    assert body["dictation"] is True


def test_config_reflects_local_mode_and_provider_availability(monkeypatch: pytest.MonkeyPatch):
    configured = Settings(
        local_user_email="you@example.com",
        anthropic_identity_token_command="print-identity-token",
        anthropic_federation_rule_id="fdrl_test",
        anthropic_organization_id="org-test",
        background_provider="anthropic",
        dictation_enabled=False,
    )
    # The instance's one settings object, as both the route and the credentials see it.
    monkeypatch.setattr(router_module, "settings", configured)
    monkeypatch.setattr(server_credentials, "settings", configured)

    response = client.get("/config")
    assert response.status_code == 200
    body = response.json()
    assert body["localLogin"] is True
    assert body["localUserEmail"] == "you@example.com"
    assert body["providers"] == {"openai": False, "anthropic": True}
    assert body["backgroundProvider"] == "anthropic"
    assert body["dictation"] is False
