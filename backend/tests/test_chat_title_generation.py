from __future__ import annotations

from app.ai.dspy_chat import enforce_title_constraints


def test_title_fallback_strips_question_preamble() -> None:
    # Simulate the model returning something too short (common failure mode).
    title = enforce_title_constraints("Serverless", fallback_message="can you explain server vs serverless?")
    assert title is not None
    # We want a meaningful 3–5 word title, not "can you explain..."
    assert "Can You" not in title
    assert 3 <= len(title.split()) <= 5


def test_title_fallback_for_empty_message_is_generic() -> None:
    title = enforce_title_constraints("", fallback_message="hi")
    assert title is not None
    assert 3 <= len(title.split()) <= 5

