"""
Tests for background_model: which model and reasoning effort each background
feature runs on, per settings.background_provider.
"""

from talleyrand.core.config import settings
from talleyrand.features.research.background_models import background_model


def test_default_provider_returns_todays_openai_models(monkeypatch):
    monkeypatch.setattr(settings, "background_provider", "openai")

    assert background_model("kickstart_brief") == background_model("kickstart_questions")
    kickstart = background_model("kickstart_brief")
    assert (kickstart.window.api_model, kickstart.reasoning_effort) == ("gpt-6-astra", "low")

    suggestions = background_model("suggestions")
    assert (suggestions.window.api_model, suggestions.reasoning_effort) == ("gpt-6-astra", "low")

    big_picture = background_model("big_picture")
    assert (big_picture.window.api_model, big_picture.reasoning_effort) == ("gpt-6-astra", "medium")

    cheat_sheet = background_model("cheat_sheet")
    assert (cheat_sheet.window.api_model, cheat_sheet.reasoning_effort) == ("gpt-6-astra", "medium")

    report = background_model("report")
    assert (report.window.api_model, report.reasoning_effort) == ("gpt-6-astra", "xhigh")

    naming = background_model("naming")
    assert (naming.window.api_model, naming.reasoning_effort) == ("gpt-5.6-luna", "none")


def test_anthropic_provider_switches_every_feature_to_claude(monkeypatch):
    monkeypatch.setattr(settings, "background_provider", "anthropic")

    for feature in (
        "kickstart_brief",
        "kickstart_questions",
        "suggestions",
        "big_picture",
        "cheat_sheet",
        "report",
        "naming",
    ):
        chosen = background_model(feature)
        assert chosen.window.provider == "anthropic"

    # Naming sends no reasoning-effort parameter at all on Anthropic, matching
    # its OpenAI "none" (no reasoning).
    assert background_model("naming").reasoning_effort is None
