"""Teaching-assistant DSPy module for the new RAG pipeline.

Orchestrates the routed cascade:

    route -> { clarify | answer_without_context | retrieve + answer_from_context }

Design notes:

- Retrieval is injected via `CourseRetriever` (constructor DI). The DSPy module
  itself stays string-in / string-out and DB-agnostic, which keeps it
  unit-testable with a mock retriever and lets us swap retrievers later
  (e.g. add a web-search path) without touching this file.
- Public entry point is `aforward(...)`. It accepts structured `CourseInfo` /
  `ConversationHistory` (we serialize to strings only at the DSPy boundary)
  and returns a structured `TeachingAssistantResult` so the endpoint has the
  metadata it needs to build `ChatCitation`s.
- DSPy modules do blocking HTTP I/O; we wrap each call in `asyncio.to_thread`
  so the FastAPI event loop stays responsive under concurrent requests.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any, Literal

import dspy
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.course_retriever import CourseRetriever, RetrievalPath
from app.rag.explicit_retrieve import (
    TARGET_LECTURE_SLUG_DESC,
    TARGET_TIMESTAMP_DESC,
)
from app.schemas.conversation_history import ConversationHistory
from app.schemas.course_info import CourseInfo
from app.schemas.retrieval import RetrievedDoc


Route = Literal["answer", "retrieve", "clarify"]


# --- Signatures ---


class RouteQuery(dspy.Signature):
    """Determine the necessary action for a student's query."""

    course_info: str = dspy.InputField()
    conversation_history: str = dspy.InputField()
    user_query: str = dspy.InputField()

    route: Route = dspy.OutputField(
        desc=(
            "'answer' if the query can be answered from general knowledge / history / "
            "course context; 'retrieve' if answering requires specific details from "
            "lecture transcripts; 'clarify' if the user query is ambiguous."
        )
    )


class AskClarification(dspy.Signature):
    course_info: str = dspy.InputField()
    conversation_history: str = dspy.InputField()
    user_query: str = dspy.InputField()

    clarification: str = dspy.OutputField(
        desc="A polite, direct question asking the student to clarify their request."
    )


class AnswerWithoutContext(dspy.Signature):
    course_info: str = dspy.InputField()
    conversation_history: str = dspy.InputField()
    user_query: str = dspy.InputField()

    answer: str = dspy.OutputField(desc="A direct, helpful answer to the student's question.")


class GenerateRetrievalDetails(dspy.Signature):
    course_info: str = dspy.InputField()
    conversation_history: str = dspy.InputField()
    user_query: str = dspy.InputField()

    contextualized_query: str = dspy.OutputField(
        desc=(
            "A self-contained rewrite of the user_query that includes any context "
            "needed to understand it standalone."
        )
    )
    lecture_routing: list[str] = dspy.OutputField(
        desc=(
            "Lecture slugs (e.g. ['L2']) from `course_info` likely to contain the answer. "
            "Usually 1, occasionally 2-3 for comparison questions. "
            "Return an empty list ONLY when no specific lecture is more relevant than "
            "the others (this triggers a course-wide fallback)."
        )
    )
    target_lecture_slug: str | None = dspy.OutputField(desc=TARGET_LECTURE_SLUG_DESC)
    target_timestamp: str | None = dspy.OutputField(desc=TARGET_TIMESTAMP_DESC)


class AnswerFromContext(dspy.Signature):
    """Answer the student's query based on retrieved course documents."""

    course_info: str = dspy.InputField()
    conversation_history: str = dspy.InputField()
    user_query: str = dspy.InputField()
    retrieved_docs: list[str] = dspy.InputField(
        desc="Relevant snippets, timestamps, or full transcripts from the lectures."
    )

    answer: str = dspy.OutputField(
        desc=(
            "Respond to the student with a direct answer (including appropriate "
            "citations and formatting). If you don't have enough information to "
            "answer with confidence, be honest and state what information is missing."
        )
    )


# --- Result ---


class TeachingAssistantResult(BaseModel):
    """Structured output for one chat turn.

    The endpoint uses `retrieved_docs` to build `ChatCitation`s (the existing
    `_attach_citation_urls` / `_attach_video_chapter_titles` helpers in
    `app/api/v1/chat.py` already accept the same metadata shape) and
    `retrieval_path` is handy for analytics and debugging.

    `debug` carries non-user-facing diagnostics: `ChainOfThought` reasoning
    strings from the router and query generator, etc. Log it in development;
    in production decide per-deployment whether to forward it to the client.
    """

    answer: str
    route: Route
    retrieval_path: RetrievalPath | None = None
    retrieved_docs: list[RetrievedDoc] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)


# --- Module ---


_ROUTE_VALUES: frozenset[str] = frozenset(("answer", "retrieve", "clarify"))


def _normalize_route(value: object) -> Route:
    """Coerce the LM's route label to a valid `Route` literal.

    Defensive fallback: when the LM emits something outside the allowed set,
    prefer `retrieve` so we at least look for evidence before answering.
    """
    r = str(value or "").strip().lower()
    if r in _ROUTE_VALUES:
        return r  # type: ignore[return-value]
    return "retrieve"


def _stash_reasoning(debug: dict[str, Any], key: str, pred: object) -> None:
    """Copy a `ChainOfThought` prediction's `reasoning` field into `debug`.

    No-op when the prediction lacks a reasoning attribute (e.g. plain `Predict`
    modules) or when the value is empty. Keeps the dict tidy.
    """
    text = str(getattr(pred, "reasoning", "") or "").strip()
    if text:
        debug[key] = text


class TeachingAssistant(dspy.Module):
    """DSPy orchestrator for one chat turn.

    Inject a `CourseRetriever` so this module is DB-agnostic and trivial to
    unit-test (pass a mock retriever that returns canned `RetrievedDoc`s).
    """

    def __init__(self, *, retriever: CourseRetriever) -> None:
        super().__init__()
        self._retriever = retriever
        self.router = dspy.ChainOfThought(RouteQuery)
        self.query_generator = dspy.ChainOfThought(GenerateRetrievalDetails)
        self.clarifier = dspy.Predict(AskClarification)
        self.answer_without_context = dspy.ChainOfThought(AnswerWithoutContext)
        self.answer_from_context = dspy.ChainOfThought(AnswerFromContext)

    async def aforward(
        self,
        *,
        db: AsyncSession,
        course_info: CourseInfo,
        conversation_history: ConversationHistory,
        user_query: str,
    ) -> TeachingAssistantResult:
        uq = (user_query or "").strip()
        if not uq:
            raise ValueError("user_query must be non-empty")

        ci_str = course_info.to_prompt_string()
        ch_str = conversation_history.to_prompt_string()

        debug: dict[str, Any] = {}

        route_pred = await asyncio.to_thread(
            self._route_raw,
            course_info=ci_str,
            conversation_history=ch_str,
            user_query=uq,
        )
        route = _normalize_route(getattr(route_pred, "route", ""))
        _stash_reasoning(debug, "router_reasoning", route_pred)

        if route == "clarify":
            answer = await asyncio.to_thread(
                self._clarify,
                course_info=ci_str,
                conversation_history=ch_str,
                user_query=uq,
            )
            return TeachingAssistantResult(answer=answer, route="clarify", debug=debug)

        if route == "answer":
            answer = await asyncio.to_thread(
                self._answer_no_ctx,
                course_info=ci_str,
                conversation_history=ch_str,
                user_query=uq,
            )
            return TeachingAssistantResult(answer=answer, route="answer", debug=debug)

        # route == "retrieve"
        params = await asyncio.to_thread(
            self._gen_retrieval_params,
            course_info=ci_str,
            conversation_history=ch_str,
            user_query=uq,
        )
        _stash_reasoning(debug, "query_gen_reasoning", params)

        decision = await self._retriever.retrieve(
            db=db,
            course_info=course_info,
            contextualized_query=(str(params.contextualized_query or "").strip() or uq),
            lecture_routing=list(params.lecture_routing or []),
            target_lecture_slug=params.target_lecture_slug,
            target_timestamp=params.target_timestamp,
        )

        if not decision.docs:
            # Terminal "no context" branch: tell the model retrieval failed so
            # it answers honestly instead of hallucinating from missing context.
            primed_query = (
                f"{uq}\n\n"
                "(System note: a retrieval lookup for specific details in the "
                "lecture transcripts returned nothing relevant. Answer carefully "
                "and tell the student what information you don't have access to.)"
            )
            answer = await asyncio.to_thread(
                self._answer_no_ctx,
                course_info=ci_str,
                conversation_history=ch_str,
                user_query=primed_query,
            )
            return TeachingAssistantResult(
                answer=answer,
                route="retrieve",
                retrieval_path="none",
                retrieved_docs=[],
                debug=debug,
            )

        rendered_docs = [d.to_prompt_string() for d in decision.docs]
        answer = await asyncio.to_thread(
            self._answer_with_ctx,
            course_info=ci_str,
            conversation_history=ch_str,
            user_query=uq,
            retrieved_docs=rendered_docs,
        )
        return TeachingAssistantResult(
            answer=answer,
            route="retrieve",
            retrieval_path=decision.path,
            retrieved_docs=list(decision.docs),
            debug=debug,
        )

    # --- Thin sync wrappers so `aforward` can dispatch them via `asyncio.to_thread`. ---

    def _route_raw(
        self,
        *,
        course_info: str,
        conversation_history: str,
        user_query: str,
    ) -> dspy.Prediction:
        """Return the raw `ChainOfThought` prediction so `aforward` can grab
        both `route` and `reasoning`. Label normalization is done by the
        caller via `_normalize_route`.
        """
        return self.router(
            course_info=course_info,
            conversation_history=conversation_history,
            user_query=user_query,
        )

    def _clarify(
        self,
        *,
        course_info: str,
        conversation_history: str,
        user_query: str,
    ) -> str:
        return str(
            self.clarifier(
                course_info=course_info,
                conversation_history=conversation_history,
                user_query=user_query,
            ).clarification
            or ""
        ).strip()

    def _answer_no_ctx(
        self,
        *,
        course_info: str,
        conversation_history: str,
        user_query: str,
    ) -> str:
        return str(
            self.answer_without_context(
                course_info=course_info,
                conversation_history=conversation_history,
                user_query=user_query,
            ).answer
            or ""
        ).strip()

    def _gen_retrieval_params(
        self,
        *,
        course_info: str,
        conversation_history: str,
        user_query: str,
    ) -> dspy.Prediction:
        return self.query_generator(
            course_info=course_info,
            conversation_history=conversation_history,
            user_query=user_query,
        )

    def _answer_with_ctx(
        self,
        *,
        course_info: str,
        conversation_history: str,
        user_query: str,
        retrieved_docs: list[str],
    ) -> str:
        return str(
            self.answer_from_context(
                course_info=course_info,
                conversation_history=conversation_history,
                user_query=user_query,
                retrieved_docs=retrieved_docs,
            ).answer
            or ""
        ).strip()


# --- Process-level singleton ---


@lru_cache(maxsize=1)
def get_teaching_assistant() -> TeachingAssistant:
    """Return a shared `TeachingAssistant` instance for this process.

    The assistant is stateless after construction (DSPy modules + a stateless
    `CourseRetriever`), so a single instance is safe to reuse across requests
    and saves us from re-building DSPy modules on every chat turn.

    Use as a FastAPI dependency:

        @router.post(...)
        async def course_chat(..., ta: TeachingAssistant = Depends(get_teaching_assistant)):
            result = await ta.aforward(...)

    Or import and call directly when DI isn't worth it.
    """
    return TeachingAssistant(retriever=CourseRetriever())
