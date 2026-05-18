"""Unit tests for `_normalize_slug_citations`.

Pure-function tests — no DB / network. We verify that the defensive recovery
pass rewrites `[L1]`-style mistakes into the canonical `[N]` form expected by
`_format_reply_with_citation_links`, and that it leaves everything else alone.
"""

from __future__ import annotations

from uuid import uuid4

from app.api.v1.chat_v2 import _normalize_slug_citations
from app.schemas.chat import ChatCitation


def _make_citation(*, slug: str, title: str | None = None) -> ChatCitation:
    return ChatCitation(
        content_id=uuid4(),
        title=title or slug,
        extra={"type": "video", "lectureSlug": slug},
    )


def test_rewrites_uppercase_slug_to_index() -> None:
    citations = [_make_citation(slug="L1"), _make_citation(slug="L2")]
    reply = "Discussed in [L1] and again in [L2]."
    out = _normalize_slug_citations(reply, citations)
    assert out == "Discussed in [1] and again in [2]."


def test_rewrites_lowercase_slug_to_index() -> None:
    citations = [_make_citation(slug="L1")]
    out = _normalize_slug_citations("See [l1] for context.", citations)
    assert out == "See [1] for context."


def test_tolerates_lecture_prefix() -> None:
    citations = [_make_citation(slug="L1")]
    out = _normalize_slug_citations("As in [Lecture L1].", citations)
    assert out == "As in [1]."


def test_pure_digit_citations_are_untouched() -> None:
    citations = [_make_citation(slug="L1"), _make_citation(slug="L2")]
    reply = "Already correct [1] and [2]."
    assert _normalize_slug_citations(reply, citations) == reply


def test_unknown_brackets_are_left_alone() -> None:
    citations = [_make_citation(slug="L1")]
    # Slug not in our citations -> untouched. Random brackets -> untouched.
    reply = "Random [TODO] and [L9] should not be rewritten."
    assert _normalize_slug_citations(reply, citations) == reply


def test_no_citations_short_circuits() -> None:
    reply = "Body with [L1] reference."
    assert _normalize_slug_citations(reply, []) == reply


def test_first_citation_wins_when_slug_repeats() -> None:
    """Same slug appears twice -> always maps to the first occurrence's index."""
    citations = [
        _make_citation(slug="L1"),  # index 1
        _make_citation(slug="L1"),  # index 2 (same slug, different range)
        _make_citation(slug="L2"),  # index 3
    ]
    out = _normalize_slug_citations("From [L1] and [L2].", citations)
    assert out == "From [1] and [3]."


def test_does_not_touch_markdown_links() -> None:
    """Markdown links use `[text](url)` — the `]` is immediately followed by
    `(`. The pass should not corrupt these even if `text` looks like a slug.
    """
    citations = [_make_citation(slug="L1")]
    reply = "See [L1 docs](https://example.com/L1) for the full chapter."
    out = _normalize_slug_citations(reply, citations)
    # The `[L1 docs]` part isn't an exact slug match (it's "L1 docs" not "L1"),
    # so it should be left alone.
    assert "[L1 docs](https://example.com/L1)" in out


def test_strips_existing_lecture_slug_in_extra_when_missing() -> None:
    """A citation without a `lectureSlug` in extras is just skipped silently."""
    citations = [
        ChatCitation(
            content_id=uuid4(),
            title="L1",
            extra={"type": "video"},  # no lectureSlug
        )
    ]
    reply = "Discussed in [L1]."
    assert _normalize_slug_citations(reply, citations) == reply
