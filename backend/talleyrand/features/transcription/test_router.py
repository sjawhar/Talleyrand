"""
Tests for the transcription router: the dictation on/off switch must gate the
whole feature before the API-key check ever runs, and otherwise pass through.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from talleyrand.core.config import settings
from talleyrand.features.auth_jwt import router as auth_app
from talleyrand.features.transcription import router as transcription_router
from talleyrand.features.transcription.dtos import TranscribeResponseDTO
from talleyrand.models.user import User


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(transcription_router.router)
    app.dependency_overrides[auth_app.require_auth] = lambda: User(id="1", email="u@example.com")
    return TestClient(app)


def _transcribe(client: TestClient, **headers: str):
    return client.post(
        "/transcription/transcribe",
        json={"audio": "YWJj", "mime_type": "audio/webm;codecs=opus"},
        headers=headers,
    )


def test_dictation_disabled_is_a_403_even_with_no_key_header(client, monkeypatch):
    monkeypatch.setattr(settings, "dictation_enabled", False)

    response = _transcribe(client)

    assert response.status_code == 403
    assert response.json()["detail"] == "Dictation is switched off on this instance."


def test_dictation_disabled_takes_precedence_over_a_present_key(client, monkeypatch):
    monkeypatch.setattr(settings, "dictation_enabled", False)

    response = _transcribe(client, **{"X-OpenAI-API-Key": "sk-test"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Dictation is switched off on this instance."


def test_dictation_enabled_still_enforces_the_key_check(client, monkeypatch):
    monkeypatch.setattr(settings, "dictation_enabled", True)

    response = _transcribe(client)

    assert response.status_code == 400
    assert "Settings" in response.json()["detail"]


def test_dictation_enabled_with_a_key_reaches_the_service(client, monkeypatch):
    monkeypatch.setattr(settings, "dictation_enabled", True)
    monkeypatch.setattr(
        transcription_router,
        "transcribe",
        AsyncMock(return_value=TranscribeResponseDTO(text="hello")),
    )

    response = _transcribe(client, **{"X-OpenAI-API-Key": "sk-test"})

    assert response.status_code == 200
    assert response.json() == {"text": "hello"}
