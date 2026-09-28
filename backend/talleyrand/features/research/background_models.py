"""
Model selection for the background features: kickstart's brief and starting
questions, follow-up and big-picture suggestions, thread cheat sheets, reports,
and case naming. Unlike a node answer, the user never picks a model for these —
they run unattended on whatever settings.background_provider selects.
"""

from dataclasses import dataclass
from typing import Literal

from talleyrand.core.config import settings
from talleyrand.core.model_settings import ModelWindow, get_model_window

BackgroundFeature = Literal[
    "kickstart_brief",
    "kickstart_questions",
    "suggestions",
    "big_picture",
    "cheat_sheet",
    "report",
    "naming",
]


@dataclass(frozen=True)
class BackgroundModel:
    """A background feature's model window and the reasoning effort it sends
    (None sends no effort parameter at all — see ReasoningEffort in
    model_settings.py)."""

    window: ModelWindow
    reasoning_effort: str | None


_GPT_6_ASTRA = get_model_window("gpt-6-astra")
_GPT_5_6_LUNA = get_model_window("gpt-5.6-luna")
_CLAUDE_SONNET_5 = get_model_window("claude-sonnet-5")
_CLAUDE_FABLE_5_1 = get_model_window("claude-fable-5-1")

# Unchanged from before background_provider existed: every feature keeps
# exactly the OpenAI model and effort it always ran on.
_OPENAI_MODELS: dict[BackgroundFeature, BackgroundModel] = {
    "kickstart_brief": BackgroundModel(_GPT_6_ASTRA, "low"),
    "kickstart_questions": BackgroundModel(_GPT_6_ASTRA, "low"),
    "suggestions": BackgroundModel(_GPT_6_ASTRA, "low"),
    "big_picture": BackgroundModel(_GPT_6_ASTRA, "medium"),
    "cheat_sheet": BackgroundModel(_GPT_6_ASTRA, "medium"),
    "report": BackgroundModel(_GPT_6_ASTRA, "xhigh"),
    "naming": BackgroundModel(_GPT_5_6_LUNA, "none"),
}

# model_settings.py's MODEL_CONFIGS defines exactly one supported effort for
# Claude Sonnet 5 ("medium") and three for Claude Fable 5.1 (medium/high/max);
# efforts here are chosen from those. The lighter, higher-volume features run
# on Sonnet at its one supported effort; naming — a one-line extraction that
# never reasoned even on GPT ("none") — sends no effort parameter at all
# (reasoning_effort=None), which is valid for any Claude model. Big-picture and
# report run on Fable, matching the OpenAI table's "medium" for big-picture and
# taking Fable's deepest available effort, "max", for report in place of the
# GPT ladder's "xhigh" (Fable's ladder does not go that high).
_ANTHROPIC_MODELS: dict[BackgroundFeature, BackgroundModel] = {
    "kickstart_brief": BackgroundModel(_CLAUDE_SONNET_5, "medium"),
    "kickstart_questions": BackgroundModel(_CLAUDE_SONNET_5, "medium"),
    "suggestions": BackgroundModel(_CLAUDE_SONNET_5, "medium"),
    "big_picture": BackgroundModel(_CLAUDE_FABLE_5_1, "medium"),
    "cheat_sheet": BackgroundModel(_CLAUDE_SONNET_5, "medium"),
    "report": BackgroundModel(_CLAUDE_FABLE_5_1, "max"),
    "naming": BackgroundModel(_CLAUDE_SONNET_5, None),
}


def background_model(feature: BackgroundFeature) -> BackgroundModel:
    """The model and reasoning effort a background feature runs on, per
    settings.background_provider."""
    table = _OPENAI_MODELS if settings.background_provider == "openai" else _ANTHROPIC_MODELS
    return table[feature]
