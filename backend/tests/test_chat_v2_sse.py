"""Pure unit tests for the SSE framing + event→wire mapping (M3).

No DB, no LLM — just the wire contract the streaming endpoint emits.
"""

from __future__ import annotations

from uuid import uuid4

from app.ai.stream_events import (
    AnswerEvent,
    CitationsEvent,
    DoneEvent,
    StatusEvent,
    ThinkingEvent,
)
from app.api.v1.chat_v2 import _event_to_payload, _sse_frame
from app.schemas.retrieval import RetrievedDoc


def test_status_event_maps_to_status_frame() -> None:
    assert _event_to_payload(StatusEvent(stage="searching", label="Searching…")) == {
        "type": "status",
        "stage": "searching",
        "label": "Searching…",
    }
    assert _event_to_payload(StatusEvent(stage="generating", label=None)) == {
        "type": "status",
        "stage": "generating",
        "label": None,
    }


def test_thinking_event_maps_to_thinking_frame() -> None:
    assert _event_to_payload(ThinkingEvent(delta="because…")) == {
        "type": "thinking",
        "delta": "because…",
    }


def test_answer_event_preserves_raw_bracket_markers() -> None:
    # The client normalizes [N] markers; the wire must carry them untouched.
    assert _event_to_payload(AnswerEvent(delta="Serverless scales [1][2].")) == {
        "type": "answer",
        "delta": "Serverless scales [1][2].",
    }


def test_citations_and_done_are_handled_inline_not_mapped() -> None:
    doc = RetrievedDoc(text="t", lecture_id=uuid4(), lecture_slug="L1", lecture_title="Intro")
    assert _event_to_payload(CitationsEvent(docs=[doc])) is None
    assert _event_to_payload(DoneEvent(answer="x", route="answer")) is None


def test_sse_frame_format() -> None:
    assert _sse_frame({"type": "answer", "delta": "hi"}) == 'data: {"type": "answer", "delta": "hi"}\n\n'


def test_sse_frame_keeps_unicode_unescaped() -> None:
    frame = _sse_frame({"type": "status", "label": "Searching…"})
    assert "Searching…" in frame
    assert frame.endswith("\n\n")
