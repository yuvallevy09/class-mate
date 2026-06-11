"""Unit tests for the course summary generator's pure helpers.

No LLM calls: covers the timestamp-stripping post-processor and the
`_build_course_context` prompt builder (ordering, truncation).
"""

from __future__ import annotations

from app.ai.course_summary_generator import _strip_timestamp_brackets
from app.services.course_summary import _build_course_context


# --- _strip_timestamp_brackets ---


def test_strips_single_timestamp_bracket() -> None:
    assert _strip_timestamp_brackets("Servers cost money [#0:10].") == "Servers cost money."


def test_strips_comma_separated_timestamp_list() -> None:
    out = _strip_timestamp_brackets("Key ideas [#0:04, #6:06, #15:15] were covered.")
    assert out == "Key ideas were covered."


def test_strips_bracket_without_hash() -> None:
    assert _strip_timestamp_brackets("See [0:04] for details.") == "See for details."


def test_leaves_non_timestamp_brackets_alone() -> None:
    text = "As shown in [Figure 3], vectors span spaces."
    assert _strip_timestamp_brackets(text) == text


def test_handles_empty_text() -> None:
    assert _strip_timestamp_brackets("") == ""


# --- _build_course_context ---


def test_context_includes_course_header_and_lectures_in_order() -> None:
    ctx = _build_course_context(
        course_name="Linear Algebra",
        course_description="Vectors and matrices.",
        lectures=[
            ("Vectors", "Covers vectors and span."),
            ("Matrices", "Covers matrix products."),
        ],
    )
    assert ctx.startswith("Course: Linear Algebra")
    assert "Course description: Vectors and matrices." in ctx
    assert "Lecture 1 — Vectors:" in ctx
    assert "Lecture 2 — Matrices:" in ctx
    assert ctx.index("Lecture 1 — Vectors:") < ctx.index("Lecture 2 — Matrices:")


def test_context_omits_missing_description() -> None:
    ctx = _build_course_context(
        course_name="Linear Algebra",
        course_description=None,
        lectures=[("Vectors", "Covers vectors.")],
    )
    assert "Course description:" not in ctx


def test_context_truncates_to_char_budget() -> None:
    lectures = [(f"Lecture title {i}", "word " * 50) for i in range(10)]
    ctx = _build_course_context(
        course_name="C",
        course_description=None,
        lectures=lectures,
        max_chars=400,
    )
    assert len(ctx) <= 400
    assert ctx.endswith("…")
    # First lecture fits, later ones were dropped.
    assert "Lecture 1 — Lecture title 0:" in ctx
    assert "Lecture title 9" not in ctx
