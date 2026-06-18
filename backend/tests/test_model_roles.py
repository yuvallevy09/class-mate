"""Unit tests for per-task model selection (app/ai/model_roles.py).

Network-free: `resolved_model_id` returns a string and `build_lm_for_role`'s
happy path just constructs a `dspy.LM` (no request). A lightweight settings
stub stands in for the full `Settings` object.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai.model_roles import (
    ANSWER_NO_CTX,
    ANSWER_WITH_CTX,
    CHAPTERS,
    CLARIFY,
    COURSE_SUMMARY,
    GEN_RETRIEVAL,
    LECTURE_SUMMARY,
    ROUTER,
    TITLE,
    build_lm_for_role,
    resolved_model_id,
)

_FLASH = "gemini/gemini-2.5-flash"
_HAIKU = "anthropic/claude-haiku-4-5"
_SONNET = "anthropic/claude-sonnet-4-6"


def _settings(**over):
    base = dict(
        model_roles_enabled=True,
        llm_provider="anthropic",
        gemini_model="gemini-2.5-flash",
        anthropic_model="claude-sonnet-4-6",
        anthropic_haiku_model="claude-haiku-4-5",
        google_api_key="g-key",
        anthropic_api_key="a-key",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    "role, expected",
    [
        (ROUTER, _HAIKU),
        (TITLE, _HAIKU),
        (CLARIFY, _HAIKU),
        (ANSWER_NO_CTX, _HAIKU),
        (CHAPTERS, _FLASH),
        (COURSE_SUMMARY, _FLASH),
        (GEN_RETRIEVAL, _SONNET),
        (ANSWER_WITH_CTX, _SONNET),
        (LECTURE_SUMMARY, _SONNET),
    ],
)
def test_roles_resolve_to_expected_models(role, expected):
    assert resolved_model_id(_settings(), role) == expected


def test_missing_tier_key_falls_back_to_global_provider():
    # Dev-like: only Google configured. Haiku/Sonnet roles (which now include
    # the router) must degrade to the global provider (gemini) rather than
    # resolve to an unusable Anthropic id.
    s = _settings(llm_provider="gemini", anthropic_api_key=None)
    assert resolved_model_id(s, ROUTER) == _FLASH
    assert resolved_model_id(s, CLARIFY) == _FLASH
    assert resolved_model_id(s, ANSWER_WITH_CTX) == _FLASH


def test_disabled_flag_uses_global_provider():
    s = _settings(model_roles_enabled=False, llm_provider="gemini")
    assert resolved_model_id(s, ANSWER_WITH_CTX) == _FLASH


def test_build_lm_for_role_sets_model_string():
    assert build_lm_for_role(_settings(), ROUTER, max_tokens=512).model == _HAIKU
    assert build_lm_for_role(_settings(), CLARIFY, max_tokens=512).model == _HAIKU
    assert build_lm_for_role(_settings(), ANSWER_WITH_CTX, max_tokens=2048).model == _SONNET
