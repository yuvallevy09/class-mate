"""Orchestration tests for `TeachingAssistant.astream` (M2 + token-streaming).

No real LLM and no DB: every LLM call in `astream` now goes through the single
seam `_astream_predict`, which we stub per predictor to yield canned
`(field, delta)` chunks then `(None, prediction)`. The real `_emit_answer`
(thinking + answer streaming + no-chunk fallback) runs on top of the stub, so
the event sequence per route is exercised end-to-end. A separate, skippable
smoke test drives the real streamify path against the configured provider.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.ai.stream_events import (
    AnswerEvent,
    CitationsEvent,
    DoneEvent,
    ThinkingEvent,
)
from app.ai.model_roles import ANSWER_WITH_CTX
from app.ai.teaching_assistant import TeachingAssistant
from app.rag.course_retriever import RetrievalDecision
from app.schemas.conversation_history import ConversationHistory
from app.schemas.course_info import CourseInfo, Lecture
from app.schemas.retrieval import RetrievedDoc


def _course_info() -> CourseInfo:
    lec = Lecture(id=uuid4(), content_id=uuid4(), slug="L1", title="Intro", transcript_ready=True)
    return CourseInfo(id=uuid4(), name="Networks", description="Intro course", lectures=[lec])


def _doc() -> RetrievedDoc:
    return RetrievedDoc(text="t", lecture_id=uuid4(), lecture_slug="L1", lecture_title="Intro")


class _FakeRetriever:
    def __init__(self, decision: RetrievalDecision):
        self._decision = decision
        self.called = False

    async def retrieve(self, **kwargs):  # noqa: ARG002
        self.called = True
        return self._decision


def _ta(decision: RetrievalDecision) -> TeachingAssistant:
    return TeachingAssistant(retriever=_FakeRetriever(decision), lm=object())


def _stub_predict(ta: TeachingAssistant, plan: dict) -> None:
    """Stub `_astream_predict`: keyed by predictor identity, yield canned
    `(field, delta)` chunks then `(None, prediction)`. KeyErrors loudly if an
    unexpected predictor is invoked."""

    async def _gen(predictor, *, listen_fields, **inputs):  # noqa: ARG001
        deltas, final = plan[id(predictor)]
        for f, d in deltas:
            yield (f, d)
        yield (None, final)

    ta._astream_predict = _gen


async def _collect(ta: TeachingAssistant):
    return [
        ev
        async for ev in ta.astream(
            db=None,
            course_info=_course_info(),
            conversation_history=ConversationHistory(turns=[]),
            user_query="what are eigenvalues?",
        )
    ]


@pytest.mark.asyncio
async def test_retrieve_with_docs_streams_thinking_and_answer() -> None:
    ta = _ta(RetrievalDecision(docs=[_doc()], path="hybrid"))
    _stub_predict(
        ta,
        {
            id(ta.router): (
                [("reasoning", "router thinks ")],
                SimpleNamespace(route="retrieve", reasoning="router thinks"),
            ),
            id(ta.query_generator): (
                [("reasoning", "picking L1")],
                SimpleNamespace(
                    contextualized_query="standalone q",
                    lecture_routing=["L1"],
                    target_lecture_slug=None,
                    target_timestamp=None,
                    reasoning="picking L1",
                ),
            ),
            id(ta.answer_from_context): (
                [("reasoning", "ans reason "), ("answer", "Hello "), ("answer", "world")],
                SimpleNamespace(answer="Hello world"),
            ),
        },
    )

    events = await _collect(ta)

    # All three reasoning blocks stream as thinking, separated by blank lines.
    thinking = "".join(e.delta for e in events if isinstance(e, ThinkingEvent))
    assert thinking == "router thinks \n\npicking L1\n\nans reason "
    assert [e.delta for e in events if isinstance(e, AnswerEvent)] == ["Hello ", "world"]

    # Citations precede the first answer delta; done is last.
    types = [type(e).__name__ for e in events]
    assert types.index("CitationsEvent") < types.index("AnswerEvent")
    assert isinstance(events[-1], DoneEvent)
    done = events[-1]
    assert done.answer == "Hello world"
    assert done.route == "retrieve"
    assert done.retrieval_path == "hybrid"
    assert len(done.retrieved_docs) == 1


@pytest.mark.asyncio
async def test_no_incremental_chunks_still_emits_full_answer() -> None:
    # answer_from_context returns everything in the terminal Prediction (no
    # incremental answer deltas). astream must still emit one AnswerEvent.
    ta = _ta(RetrievalDecision(docs=[_doc()], path="hybrid"))
    _stub_predict(
        ta,
        {
            id(ta.router): ([], SimpleNamespace(route="retrieve", reasoning="")),
            id(ta.query_generator): (
                [],
                SimpleNamespace(
                    contextualized_query="q",
                    lecture_routing=["L1"],
                    target_lecture_slug=None,
                    target_timestamp=None,
                    reasoning="",
                ),
            ),
            id(ta.answer_from_context): ([], SimpleNamespace(answer="The whole answer.")),
        },
    )

    events = await _collect(ta)
    answer_events = [e for e in events if isinstance(e, AnswerEvent)]
    assert len(answer_events) == 1
    assert answer_events[0].delta == "The whole answer."
    assert events[-1].answer == "The whole answer."


@pytest.mark.asyncio
async def test_retrieve_no_docs_streams_fallback_answer() -> None:
    ta = _ta(RetrievalDecision(docs=[], path="none"))
    _stub_predict(
        ta,
        {
            id(ta.router): ([], SimpleNamespace(route="retrieve", reasoning="")),
            id(ta.query_generator): (
                [],
                SimpleNamespace(
                    contextualized_query="q",
                    lecture_routing=["L1"],
                    target_lecture_slug=None,
                    target_timestamp=None,
                    reasoning="",
                ),
            ),
            id(ta.answer_without_context): (
                [("answer", "Not in the lectures.")],
                SimpleNamespace(answer="Not in the lectures."),
            ),
        },
    )

    events = await _collect(ta)
    assert not any(isinstance(e, CitationsEvent) for e in events)
    assert [e.delta for e in events if isinstance(e, AnswerEvent)] == ["Not in the lectures."]
    assert events[-1].retrieval_path == "none"
    assert events[-1].retrieved_docs == []
    assert events[-1].answer == "Not in the lectures."


@pytest.mark.asyncio
async def test_answer_route_streams_without_retrieval() -> None:
    ta = _ta(RetrievalDecision(docs=[], path="none"))
    _stub_predict(
        ta,
        {
            id(ta.router): (
                [("reasoning", "general knowledge")],
                SimpleNamespace(route="answer", reasoning="general knowledge"),
            ),
            id(ta.answer_without_context): (
                [("reasoning", "r "), ("answer", "A direct "), ("answer", "answer.")],
                SimpleNamespace(answer="A direct answer."),
            ),
        },
    )

    events = await _collect(ta)
    # Router reasoning + answer reasoning stream, separated by a blank line.
    thinking = "".join(e.delta for e in events if isinstance(e, ThinkingEvent))
    assert thinking == "general knowledge\n\nr "
    assert [e.delta for e in events if isinstance(e, AnswerEvent)] == ["A direct ", "answer."]
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].route == "answer"
    assert events[-1].answer == "A direct answer."
    assert ta._retriever.called is False


@pytest.mark.asyncio
async def test_clarify_route_streams_clarification() -> None:
    ta = _ta(RetrievalDecision(docs=[], path="none"))
    _stub_predict(
        ta,
        {
            # Empty router reasoning => no ThinkingEvent.
            id(ta.router): ([], SimpleNamespace(route="clarify", reasoning="")),
            id(ta.clarifier): (
                [("clarification", "Which lecture did you mean?")],
                SimpleNamespace(clarification="Which lecture did you mean?"),
            ),
        },
    )

    events = await _collect(ta)
    assert [type(e) for e in events] == [AnswerEvent, DoneEvent]
    assert events[0].delta == "Which lecture did you mean?"
    assert events[-1].route == "clarify"
    assert events[-1].answer == "Which lecture did you mean?"


@pytest.mark.asyncio
async def test_empty_query_raises() -> None:
    ta = _ta(RetrievalDecision(docs=[], path="none"))
    with pytest.raises(ValueError):
        async for _ in ta.astream(
            db=None,
            course_info=_course_info(),
            conversation_history=ConversationHistory(turns=[]),
            user_query="   ",
        ):
            pass


@pytest.mark.asyncio
async def test_emit_answer_smoke_against_real_provider() -> None:
    """Drives the real streamify path via `_emit_answer`. Skipped without a key."""
    from app.ai.llm import effective_llm_api_key
    from app.core.settings import get_settings

    settings = get_settings()
    try:
        effective_llm_api_key(settings)
    except ValueError:
        pytest.skip("No LLM provider API key configured.")

    ta = TeachingAssistant(retriever=_FakeRetriever(RetrievalDecision(docs=[], path="none")), lm=None)
    out: dict[str, str] = {}
    events = [
        ev
        async for ev in ta._emit_answer(
            ta.answer_from_context,
            lm=ta._lm_for(ANSWER_WITH_CTX),
            answer_field="answer",
            out=out,
            course_info="Course: Test",
            conversation_history="(no prior messages)",
            user_query="Explain the TCP three-way handshake in 3-4 sentences.",
            retrieved_docs=["[1] [Source: L1] The TCP handshake is SYN, SYN-ACK, ACK."],
        )
    ]

    # Deterministic: the streamify path runs and produces a non-empty answer.
    assert out.get("answer")
    assert any(isinstance(e, (AnswerEvent, ThinkingEvent)) for e in events)
