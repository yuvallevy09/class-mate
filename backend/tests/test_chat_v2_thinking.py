"""Unit tests for `_build_thinking` (Tier 0 thinking surfacing).

Pure-function tests — no DB / network. Verify the plain-text reasoning is
composed from the cascade's debug + retrieval metadata, and that it degrades to
None when there's nothing substantive (so the UI hides the panel).
"""

from __future__ import annotations

from uuid import uuid4

from app.api.v1.chat_v2 import _build_thinking
from app.schemas.retrieval import RetrievedDoc


def _doc(slug: str) -> RetrievedDoc:
    return RetrievedDoc(
        text="transcript text",
        lecture_id=uuid4(),
        lecture_slug=slug,
        lecture_title=f"Lecture {slug}",
    )


def test_retrieve_route_summarizes_where_and_why() -> None:
    out = _build_thinking(
        route="retrieve",
        retrieval_path="hybrid",
        retrieved_docs=[_doc("L2"), _doc("L3"), _doc("L2")],  # L2 dedupes
        debug={"query_gen_reasoning": "The question is about TCP, covered in L2/L3."},
    )
    assert out is not None
    assert "Searched lectures L2, L3 — found 3 relevant passages." in out
    assert "The question is about TCP, covered in L2/L3." in out


def test_retrieve_route_single_passage_singular() -> None:
    out = _build_thinking(
        route="retrieve",
        retrieval_path="full_lecture",
        retrieved_docs=[_doc("L1")],
        debug={},
    )
    assert out == "Searched lectures L1 — found 1 relevant passage."


def test_retrieve_route_none_path_says_nothing_found() -> None:
    out = _build_thinking(
        route="retrieve",
        retrieval_path="none",
        retrieved_docs=[],
        debug={"query_gen_reasoning": "Looked for the topic."},
    )
    assert out is not None
    assert "found nothing relevant" in out
    assert "Looked for the topic." in out


def test_answer_route_uses_router_reasoning() -> None:
    out = _build_thinking(
        route="answer",
        retrieval_path=None,
        retrieved_docs=[],
        debug={"router_reasoning": "This is general knowledge; no lecture needed."},
    )
    assert out == "This is general knowledge; no lecture needed."


def test_returns_none_when_no_substance() -> None:
    assert (
        _build_thinking(route="answer", retrieval_path=None, retrieved_docs=[], debug={})
        is None
    )
    # Empty/whitespace reasoning is treated as absent.
    assert (
        _build_thinking(
            route="clarify", retrieval_path=None, retrieved_docs=[], debug={"router_reasoning": "   "}
        )
        is None
    )
