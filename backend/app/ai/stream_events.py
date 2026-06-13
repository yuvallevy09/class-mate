"""Typed events emitted by `TeachingAssistant.astream`.

The streaming cascade yields these as it runs. They're intentionally transport-
agnostic: the v2 streaming endpoint (M3) translates each into an SSE `data:`
frame, and the frontend's existing handler contract
(`onStatus`/`onThinkingDelta`/`onCitations`/`onAnswerDelta`/`onDone`) maps 1:1
onto them. Kept in their own module so the endpoint can import them without
pulling in DSPy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union

from app.rag.course_retriever import RetrievalPath
from app.schemas.retrieval import RetrievedDoc


# Mirrors the frontend's `onStatus` stages.
StatusStage = Literal["searching", "generating"]


@dataclass(frozen=True)
class StatusEvent:
    """Pipeline progress — "Searching course materials…", "generating", etc."""

    stage: StatusStage
    label: str | None = None


@dataclass(frozen=True)
class ThinkingEvent:
    """A chunk of reasoning to append to the thinking panel.

    Carries router/query-gen rationale (one chunk each) and, on the retrieve
    path, the answer model's `reasoning` field streamed token-by-token.
    """

    delta: str


@dataclass(frozen=True)
class CitationsEvent:
    """The retrieved docs, emitted as soon as retrieval returns (before answer
    tokens) so the client can render Sources while the answer streams in."""

    docs: list[RetrievedDoc]


@dataclass(frozen=True)
class AnswerEvent:
    """A chunk of the user-facing answer to append."""

    delta: str


@dataclass(frozen=True)
class DoneEvent:
    """Terminal event. Carries the accumulated full answer plus the metadata the
    endpoint needs to run citation post-processing and persist the message."""

    answer: str
    route: str
    retrieval_path: RetrievalPath | None = None
    retrieved_docs: list[RetrievedDoc] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


Event = Union[StatusEvent, ThinkingEvent, CitationsEvent, AnswerEvent, DoneEvent]
