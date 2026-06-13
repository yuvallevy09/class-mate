"""Unit tests for the `CourseRetriever` cascade — specifically the course-wide
hybrid fallback (fix A).

No DB needed: `retrieve_explicitly`, `get_lecture_text`, and
`_perform_hybrid_search` are module-level in `course_retriever`, so we monkeypatch
them to drive the cascade deterministically and assert on which search actually
ran (via recorded `lecture_slugs`).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.rag import course_retriever as cr
from app.rag.course_retriever import CourseRetriever
from app.schemas.course_info import CourseInfo, Lecture
from app.schemas.retrieval import RetrievedDoc


def _course_info() -> CourseInfo:
    lec = Lecture(
        id=uuid4(),
        content_id=uuid4(),
        slug="L1",
        title="Intro",
        transcript_ready=True,
    )
    return CourseInfo(id=uuid4(), name="Networks", description=None, lectures=[lec])


def _doc() -> RetrievedDoc:
    return RetrievedDoc(
        text="some retrieved transcript text",
        lecture_id=uuid4(),
        lecture_slug="L5",
        lecture_title="Some Lecture",
    )


class _HybridRecorder:
    """Async stand-in for `_perform_hybrid_search` that records the `lecture_slugs`
    of each call and returns canned docs based on a caller-supplied rule."""

    def __init__(self, rule):
        self.calls: list[list[str] | None] = []
        self._rule = rule

    async def __call__(self, *, db, course_info, query, lecture_slugs, cfg=None):  # noqa: ARG002
        self.calls.append(lecture_slugs)
        return self._rule(lecture_slugs)


@pytest.fixture
def _no_explicit_no_full(monkeypatch):
    """Force the cascade past the explicit + full-lecture paths into hybrid."""

    async def _no_explicit(**kwargs):  # noqa: ARG001
        return []

    async def _no_full(**kwargs):  # noqa: ARG001
        return [], False

    monkeypatch.setattr(cr, "retrieve_explicitly", _no_explicit)
    monkeypatch.setattr(cr, "get_lecture_text", _no_full)


async def _run(routing: list[str]):
    return await CourseRetriever().retrieve(
        db=object(),
        course_info=_course_info(),
        contextualized_query="what is a TCP handshake?",
        lecture_routing=routing,
        target_lecture_slug=None,
        target_timestamp=None,
    )


@pytest.mark.asyncio
async def test_course_wide_fallback_fires_when_scoped_hybrid_empty(
    _no_explicit_no_full, monkeypatch
) -> None:
    # Scoped search (truthy slugs) misses; course-wide (falsy slugs) hits.
    recorder = _HybridRecorder(lambda slugs: [] if slugs else [_doc()])
    monkeypatch.setattr(cr, "_perform_hybrid_search", recorder)

    decision = await _run(["L1"])

    assert decision.path == "hybrid_course_wide"
    assert len(decision.docs) == 1
    # Two searches: scoped to ["L1"], then widened to None.
    assert recorder.calls == [["L1"], None]


@pytest.mark.asyncio
async def test_no_fallback_when_scoped_hybrid_succeeds(
    _no_explicit_no_full, monkeypatch
) -> None:
    recorder = _HybridRecorder(lambda slugs: [_doc()])  # scoped already hits
    monkeypatch.setattr(cr, "_perform_hybrid_search", recorder)

    decision = await _run(["L1"])

    assert decision.path == "hybrid"
    # Only the scoped search ran — no wasteful course-wide retry.
    assert recorder.calls == [["L1"]]


@pytest.mark.asyncio
async def test_no_retry_when_routing_empty(_no_explicit_no_full, monkeypatch) -> None:
    # Empty routing => the first hybrid search is already course-wide; a retry
    # would be identical work, so it must not happen.
    recorder = _HybridRecorder(lambda slugs: [])
    monkeypatch.setattr(cr, "_perform_hybrid_search", recorder)

    decision = await _run([])

    assert decision.path == "none"
    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_genuine_miss_returns_none_after_widening(
    _no_explicit_no_full, monkeypatch
) -> None:
    recorder = _HybridRecorder(lambda slugs: [])  # nothing anywhere
    monkeypatch.setattr(cr, "_perform_hybrid_search", recorder)

    decision = await _run(["L1"])

    assert decision.path == "none"
    assert decision.docs == []
    # Tried scoped, then widened, then gave up.
    assert recorder.calls == [["L1"], None]
