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

from app.ai.dspy_chat import _effective_gemini_api_key, _litellm_gemini_model
from app.core.settings import Settings, get_settings
from app.rag.course_retriever import CourseRetriever, RetrievalPath
from app.rag.explicit_retrieve import (
    TARGET_LECTURE_SLUG_DESC,
    TARGET_TIMESTAMP_DESC,
)
from app.schemas.conversation_history import ConversationHistory
from app.schemas.course_info import CourseInfo
from app.schemas.retrieval import RetrievedDoc


# Generous default so structured-output signatures (especially the long
# answer-from-context one) aren't truncated mid-JSON. The router and clarifier
# would do fine with less, but using a single budget keeps the LM builder simple.
_DEFAULT_MAX_TOKENS = 2048


Route = Literal["answer", "retrieve", "clarify"]


# --- Signatures ---


class RouteQuery(dspy.Signature):
    """Determine the necessary action for a student's query."""

    course_info: str = dspy.InputField()
    conversation_history: str = dspy.InputField()
    user_query: str = dspy.InputField()

    route: Route = dspy.OutputField(
        desc=(
            "'answer' if the query can be answered from general knowledge;"
            "'retrieve' if answering requires specific details from lecture transcripts;"
            "'clarify' if the user query is ambiguous."
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
        desc=(
            "Relevant snippets, timestamps, or full transcripts from the lectures. "
            "Each entry begins with a NUMERIC citation key in square brackets — "
            "'[1]', '[2]', '[3]', ... — followed by '[Source: ...]' metadata and "
            "the text. Use ONLY the numeric key when citing in `answer`."
        )
    )

    answer: str = dspy.OutputField(
        desc=(
            "Direct, helpful answer to the student.\n\n"
            "CITATION RULES (strict — the post-processor depends on this):\n"
            "1. To cite a retrieved doc, write its NUMERIC key in square "
            "brackets immediately after the claim it supports. "
            "Examples: '[1]', '[2]', '[1][3]'.\n"
            "2. Citation keys are DIGITS ONLY. Do NOT cite by lecture slug. "
            "WRONG: '[L1]', '[L2]', '[Lecture 1]'. RIGHT: '[1]', '[2]'.\n"
            "3. Do NOT write raw timestamps like '#0:08' or '(0:04)' — the "
            "numeric citation already links to the exact video moment.\n"
            "4. Do NOT echo the '[Source: ...]' metadata block in your answer; "
            "it's for your reference only.\n"
            "5. Do NOT invent citation numbers that aren't in `retrieved_docs`.\n\n"
            "Good example: 'Serverless is an architecture where the cloud "
            "provider chooses the hardware and handles scaling [1].'\n"
            "Bad example: 'You can find this discussion in [L1] Json Title "
            "Server vs Serverless (0:04).'\n\n"
            "If `retrieved_docs` is insufficient to answer confidently, say so "
            "and state what's missing instead of guessing."
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

    The LM is built lazily from `Settings` on first use and reused across
    requests; tests can short-circuit this by passing a pre-built `lm` (e.g.
    a DSPy `DummyLM`) to the constructor.
    """

    def __init__(
        self,
        *,
        retriever: CourseRetriever,
        lm: dspy.LM | None = None,
    ) -> None:
        super().__init__()
        self._retriever = retriever
        self._lm: dspy.LM | None = lm
        self.router = dspy.ChainOfThought(RouteQuery)
        self.query_generator = dspy.ChainOfThought(GenerateRetrievalDetails)
        self.clarifier = dspy.Predict(AskClarification)
        self.answer_without_context = dspy.ChainOfThought(AnswerWithoutContext)
        self.answer_from_context = dspy.ChainOfThought(AnswerFromContext)

    def _get_lm(self) -> dspy.LM:
        """Lazy-build the LM from `Settings` and cache on the instance.

        Built once on first call (per process via the module singleton) so we
        don't recreate the LiteLLM client on every chat turn. `Settings` is
        read at first use rather than at construction time to keep import-time
        side effects minimal (handy in tests).
        """
        if self._lm is None:
            settings: Settings = get_settings()
            _effective_gemini_api_key(settings)  # fails fast if the key is missing
            self._lm = dspy.LM(
                model=_litellm_gemini_model(settings.gemini_model),
                temperature=float(settings.chat_temperature),
                max_tokens=_DEFAULT_MAX_TOKENS,
                num_retries=2,
            )
        return self._lm

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

        # 1-based indexing aligns with `_format_reply_with_citation_links`,
        # which numbers citations from 1 in the order they appear in the
        # response's `citations` array (built from these same docs in
        # `chat_v2._docs_to_citations`).
        rendered_docs = [
            d.to_prompt_string(index=i) for i, d in enumerate(decision.docs, start=1)
        ]
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

    # Each helper binds the LM via `dspy.settings.context(lm=...)` so the
    # configuration is set in the worker thread where the DSPy call actually
    # runs (cf. `asyncio.to_thread` in `aforward`). This sidesteps any
    # thread-local vs contextvar differences between DSPy versions.

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
        with dspy.settings.context(lm=self._get_lm()):
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
        with dspy.settings.context(lm=self._get_lm()):
            pred = self.clarifier(
                course_info=course_info,
                conversation_history=conversation_history,
                user_query=user_query,
            )
        return str(pred.clarification or "").strip()

    def _answer_no_ctx(
        self,
        *,
        course_info: str,
        conversation_history: str,
        user_query: str,
    ) -> str:
        with dspy.settings.context(lm=self._get_lm()):
            pred = self.answer_without_context(
                course_info=course_info,
                conversation_history=conversation_history,
                user_query=user_query,
            )
        return str(pred.answer or "").strip()

    def _gen_retrieval_params(
        self,
        *,
        course_info: str,
        conversation_history: str,
        user_query: str,
    ) -> dspy.Prediction:
        with dspy.settings.context(lm=self._get_lm()):
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
        with dspy.settings.context(lm=self._get_lm()):
            pred = self.answer_from_context(
                course_info=course_info,
                conversation_history=conversation_history,
                user_query=user_query,
                retrieved_docs=retrieved_docs,
            )
        return str(pred.answer or "").strip()


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
