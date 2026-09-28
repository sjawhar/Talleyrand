"""
When this instance has no GOOGLE_REDIRECT_URI (single-user local mode, no
Google project), the Google routes must fail closed before reaching Google
instead of sending a broken request with redirect_uri=None.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from talleyrand.core.config import Settings

from . import app as app_module
from . import service
from .app import NOT_CONFIGURED_DETAIL, router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


@pytest.fixture(autouse=True)
def _unconfigured(monkeypatch: pytest.MonkeyPatch):
    unconfigured = Settings(google_redirect_uri=None, local_user_email="you@example.com")
    monkeypatch.setattr(app_module, "settings", unconfigured)
    monkeypatch.setattr(service, "settings", unconfigured)


def test_redirect_url_is_unavailable_without_a_redirect_uri():
    response = client.get(
        "/oauth/google/redirect_url?next_url=http://localhost:3000/home",
        follow_redirects=False,
    )
    assert response.status_code == 503
    assert response.json()["detail"] == NOT_CONFIGURED_DETAIL


def test_callback_is_unavailable_without_a_redirect_uri():
    response = client.get(
        "/oauth/google/callback?state=irrelevant&code=irrelevant",
        follow_redirects=False,
    )
    assert response.status_code == 503
    assert response.json()["detail"] == NOT_CONFIGURED_DETAIL
