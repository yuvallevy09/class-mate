"""Video-mode tests for `TeachingAssistant` (Slice 3).

Two layers:
- Pure unit tests for the shared helpers (`_apply_soft_scope`, `_merge_recent`,
  `_viewing_note`) — no I/O.
- Flow tests for `astream` and `aforward` with the LLM seam and `_recent_window`
  stubbed (no real LLM, no DB), asserting soft-scope routing, the recent-window
  anchor, and the empty-retrieval-but-recent-present path.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.ai.stream_events import AnswerEvent, CitationsEvent, DoneEvent
from app.ai.teaching_assistant import (
    TeachingAssistant,
    _apply_soft_scope,
    _merge_recent,
    _viewing_note,
)
from app.rag.course_retriever import RetrievalDecision
from app.schemas.conversation_history import ConversationHistory
from app.schemas.course_info import CourseInfo, Lecture
from app.schemas.retrieval import RetrievedDoc
from app.schemas.viewing_context import ViewingContext


LEC_ID = uuid4()


def _course_info() -> CourseInfo:
    return CourseInfo(
        id=uuid4(),
        name="ML",
        lectures=[
            Lecture(id=LEC_ID, content_id=uuid4(), slug="L3", title="Backprop", transcript_ready=True),
            Lecture(id=uuid4(), content_id=uuid4(), slug="L5", title="CNNs", transcript_ready=True),
        ],
    )


def _viewing(ts: float | None = 765.0) -> ViewingContext:
    return ViewingContext(
        lecture_id=LEC_ID, lecture_slug="L3", lecture_title="Backprop", timestamp_sec=ts
    )


def _rdoc(*, slug="L3", lid=LEC_ID, start=None, end=None, text="t") -> RetrievedDoc:
    return RetrievedDoc(
        text=text, lecture_id=lid, lecture_slug=slug, lecture_title="x",
        start_sec=start, end_sec=end,
    )


# --- Pure helpers ---


def test_apply_soft_scope_no_viewing_unchanged() -> None:
    assert _apply_soft_scope(["L5"], None) == ["L5"]


def test_apply_soft_scope_empty_routing_stays_course_wide() -> None:
    # Empty routing = the LLM's "search course-wide" signal; video mode must NOT
    # collapse it to the watched lecture.
    assert _apply_soft_scope([], _viewing()) == []


def test_apply_soft_scope_prepends_watched_lecture() -> None:
    assert _apply_soft_scope(["L5"], _viewing()) == ["L3", "L5"]


def test_apply_soft_scope_no_duplicate_when_already_present() -> None:
    assert _apply_soft_scope(["L3", "L5"], _viewing()) == ["L3", "L5"]


def test_viewing_note_variants() -> None:
    note = _viewing_note(_viewing(125.0))
    assert "L3: Backprop" in note and "2:05" in note
    no_ts = _viewing_note(_viewing(ts=None))
    assert "L3: Backprop" in no_ts and "2:05" not in no_ts
    assert _viewing_note(None) == ""


def test_merge_recent_empty_recent_passthrough() -> None:
    docs = [_rdoc()]
    assert _merge_recent([], docs) == docs


def test_merge_recent_drops_anchor_when_covered_by_retrieved() -> None:
    # Near the start: the recent window (0:00–0:08) sits inside a retrieved
    # chunk (0:00–0:46). The anchor would duplicate it -> dropped.
    recent = [_rdoc(start=0.0, end=8.0, text="recent")]
    covering = _rdoc(start=0.0, end=46.0, text="chunk")
    merged = _merge_recent(recent, [covering])
    assert [d.text for d in merged] == ["chunk"]


def test_merge_recent_prepends_and_drops_contained() -> None:
    recent = [_rdoc(start=140.0, end=260.0, text="recent")]
    contained = _rdoc(start=200.0, end=230.0, text="inside")  # dropped
    extending = _rdoc(start=250.0, end=300.0, text="partial")  # kept (extends past)
    other_lecture = _rdoc(lid=uuid4(), start=210.0, end=220.0, text="otherlec")  # kept
    merged = _merge_recent(recent, [contained, extending, other_lecture])
    texts = [d.text for d in merged]
    assert texts[0] == "recent"  # anchor first -> becomes [1]
    assert "inside" not in texts
    assert "partial" in texts and "otherlec" in texts


# --- Flow: astream + aforward ---


class _CapRetriever:
    """Fake retriever that records the kwargs it was called with."""

    def __init__(self, decision: RetrievalDecision):
        self._decision = decision
        self.kwargs: dict | None = None

    async def retrieve(self, **kwargs):
        self.kwargs = kwargs
        return self._decision


def _stub_predict(ta: TeachingAssistant, plan: dict) -> None:
    async def _gen(predictor, *, listen_fields, **inputs):  # noqa: ARG001
        deltas, final = plan[id(predictor)]
        for f, d in deltas:
            yield (f, d)
        yield (None, final)

    ta._astream_predict = _gen


def _stub_recent(ta: TeachingAssistant, docs: list[RetrievedDoc]) -> None:
    async def _rw(**kwargs):  # noqa: ARG001
        return docs

    ta._recent_window = _rw


@pytest.mark.asyncio
async def test_astream_video_mode_softscope_and_recent_anchor() -> None:
    retr = _CapRetriever(RetrievalDecision(docs=[_rdoc(start=10.0, end=20.0, text="hybrid hit")], path="hybrid"))
    ta = TeachingAssistant(retriever=retr, lm=object())
    _stub_recent(ta, [_rdoc(start=645.0, end=765.0, text="just watched")])
    _stub_predict(
        ta,
        {
            id(ta.router): ([], SimpleNamespace(route="retrieve", reasoning="")),
            id(ta.query_generator): (
                [],
                SimpleNamespace(
                    contextualized_query="q", lecture_routing=["L5"],
                    target_lecture_slug=None, target_timestamp=None, reasoning="",
                ),
            ),
            id(ta.answer_from_context): ([("answer", "ok")], SimpleNamespace(answer="ok")),
        },
    )

    events = [
        ev
        async for ev in ta.astream(
            db=None,
            course_info=_course_info(),
            conversation_history=ConversationHistory(turns=[]),
            user_query="explain this",
            viewing=_viewing(),
        )
    ]

    # Soft scope: watched L3 prepended to the LLM's [L5].
    assert retr.kwargs["lecture_routing"] == ["L3", "L5"]
    # Recent window is citation [1], merged ahead of the hybrid hit.
    cite = next(e for e in events if isinstance(e, CitationsEvent))
    assert [d.text for d in cite.docs] == ["just watched", "hybrid hit"]
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.retrieved_docs[0].text == "just watched"
    assert done.retrieval_path == "hybrid"


@pytest.mark.asyncio
async def test_astream_video_mode_recent_only_when_retrieval_empty() -> None:
    retr = _CapRetriever(RetrievalDecision(docs=[], path="none"))
    ta = TeachingAssistant(retriever=retr, lm=object())
    _stub_recent(ta, [_rdoc(start=645.0, end=765.0, text="just watched")])
    _stub_predict(
        ta,
        {
            id(ta.router): ([], SimpleNamespace(route="retrieve", reasoning="")),
            id(ta.query_generator): (
                [],
                SimpleNamespace(
                    contextualized_query="q", lecture_routing=[],
                    target_lecture_slug=None, target_timestamp=None, reasoning="",
                ),
            ),
            id(ta.answer_from_context): ([("answer", "grounded")], SimpleNamespace(answer="grounded")),
        },
    )

    events = [
        ev
        async for ev in ta.astream(
            db=None,
            course_info=_course_info(),
            conversation_history=ConversationHistory(turns=[]),
            user_query="what did he just say?",
            viewing=_viewing(),
        )
    ]

    # Empty routing stays course-wide (not collapsed to the watched lecture).
    assert retr.kwargs["lecture_routing"] == []
    # Even with empty retrieval, the recent window carries the answer's context.
    cite = next(e for e in events if isinstance(e, CitationsEvent))
    assert [d.text for d in cite.docs] == ["just watched"]
    done = events[-1]
    assert done.retrieval_path == "explicit"
    assert len(done.retrieved_docs) == 1


@pytest.mark.asyncio
async def test_aforward_video_mode_wires_softscope_recent_and_anchor() -> None:
    retr = _CapRetriever(RetrievalDecision(docs=[_rdoc(start=10.0, end=20.0, text="hybrid hit")], path="hybrid"))
    ta = TeachingAssistant(retriever=retr, lm=object())
    ta._route_raw = lambda **k: SimpleNamespace(route="retrieve", reasoning="")
    ta._gen_retrieval_params = lambda **k: SimpleNamespace(
        contextualized_query="q", lecture_routing=["L5"],
        target_lecture_slug=None, target_timestamp=None, reasoning="",
    )
    _stub_recent(ta, [_rdoc(start=645.0, end=765.0, text="just watched")])
    captured: dict = {}

    def _awc(**k):
        captured.update(k)
        return "answer [1]"

    ta._answer_with_ctx = _awc

    res = await ta.aforward(
        db=None,
        course_info=_course_info(),
        conversation_history=ConversationHistory(turns=[]),
        user_query="explain this",
        viewing=_viewing(),
    )

    assert retr.kwargs["lecture_routing"] == ["L3", "L5"]
    assert res.retrieved_docs[0].text == "just watched"
    assert res.retrieval_path == "hybrid"
    assert res.debug.get("video_mode") is True
    assert res.debug.get("recent_window_attached") is True
    # Anchor line is injected into the answer-from-context course view.
    assert "currently watching L3" in captured["course_info"]
