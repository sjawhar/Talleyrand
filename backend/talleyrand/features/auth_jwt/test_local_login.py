"""
/auth/local-login issues tokens for the single configured local user, bypassing
Google entirely. It must not exist at all when local mode is off, and the
tokens it mints must be indistinguishable from any other session's to the
rest of the app (/auth/me accepts them and reports the configured identity).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from talleyrand.core.config import Settings

from . import router as router_module
from .router import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_local_login_is_not_found_when_local_mode_is_off():
    response = client.post("/auth/local-login")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not found"


def test_local_login_issues_tokens_that_identify_the_configured_user(
    monkeypatch: pytest.MonkeyPatch,
):
    configured = Settings(local_user_email="you@example.com", local_user_name="Local User")
    monkeypatch.setattr(router_module, "settings", configured)

    response = client.post("/auth/local-login")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "you@example.com"
    assert me.json()["name"] == "Local User"
