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
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any, Literal

import dspy
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import build_lm
from app.ai.model_roles import (
    ANSWER_NO_CTX,
    ANSWER_WITH_CTX,
    CLARIFY,
    GEN_RETRIEVAL,
    ROUTER,
    build_lm_for_role,
    resolved_model_id,
)
from app.ai.stream_events import (
    AnswerEvent,
    CitationsEvent,
    DoneEvent,
    Event,
    StatusEvent,
    ThinkingEvent,
)
from app.core.settings import Settings, get_settings
from app.rag.course_retriever import CourseRetriever, RetrievalPath
from app.rag.explicit_retrieve import (
    TARGET_LECTURE_SLUG_DESC,
    TARGET_TIMESTAMP_DESC,
    retrieve_recent_window,
)
from app.schemas.conversation_history import ConversationHistory
from app.schemas.course_info import CourseInfo
from app.schemas.retrieval import RetrievedDoc
from app.schemas.viewing_context import ViewingContext


# Generous default so structured-output signatures (especially the long
# answer-from-context one) aren't truncated mid-JSON. The router and clarifier
# would do fine with less, but using a single budget keeps the LM builder simple.
_DEFAULT_MAX_TOKENS = 2048

# Per-role output budgets. The answer signatures and retrieval-param generation
# keep the generous default (avoid mid-JSON truncation); the router and clarifier
# emit far less, so they get smaller budgets. Roles not listed use the default.
_ROLE_MAX_TOKENS: dict[str, int] = {
    ROUTER: 512,
    CLARIFY: 1024,
    ANSWER_NO_CTX: _DEFAULT_MAX_TOKENS,
    GEN_RETRIEVAL: _DEFAULT_MAX_TOKENS,
    ANSWER_WITH_CTX: _DEFAULT_MAX_TOKENS,
}


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
    `attach_citation_urls` / `attach_video_chapter_titles` helpers in
    `app/services/chat_citations.py` already accept the same metadata shape) and
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


# --- Video-mode helpers ---
#
# Pure functions (no I/O) shared by `aforward` and `astream` so video-mode
# behavior stays identical across the blocking and streaming paths.


def _fmt_mmss(seconds: float | None) -> str:
    try:
        s = max(0.0, float(seconds or 0.0))
    except (TypeError, ValueError):
        s = 0.0
    return f"{int(s // 60)}:{int(s % 60):02d}"


def _viewing_note(viewing: ViewingContext | None) -> str:
    """One-line anchor describing what the student is watching, or '' when not
    in video mode.

    Prefixed to the CourseInfo views (via `_view`) so every signature can
    resolve deictic references — "this", "that", "what he just said" — to the
    current lecture and playback position.
    """
    if viewing is None:
        return ""
    where = f"{viewing.lecture_slug}: {viewing.lecture_title}"
    if viewing.timestamp_sec is not None:
        return (
            f"The student is currently watching {where} at "
            f"{_fmt_mmss(viewing.timestamp_sec)} (video player open)."
        )
    return f"The student is currently watching {where} (video player open)."


def _view(view: str, note: str) -> str:
    """Prefix a rendered CourseInfo view with the viewing note when present."""
    return f"{note}\n\n{view}" if note else view


def _apply_soft_scope(
    lecture_routing: list[str], viewing: ViewingContext | None
) -> list[str]:
    """Soft-scope retrieval toward the watched lecture without jailing it.

    - Not in video mode: routing unchanged.
    - LLM emitted an empty routing (its signal for "no lecture is more relevant —
      search course-wide"): preserve it, so course-wide questions asked while
      watching still reach the whole course (the `CourseRetriever` treats an
      empty slug set as course-wide).
    - LLM scoped to specific lectures: ensure the watched lecture is in the set
      (preferred first), so in-lecture questions stay anchored while the model's
      own picks — and the course-wide empty-result fallback — keep other
      lectures reachable.
    """
    routing = [s for s in (lecture_routing or []) if isinstance(s, str) and s.strip()]
    if viewing is None or not routing:
        return routing
    if viewing.lecture_slug in routing:
        return routing
    return [viewing.lecture_slug, *routing]


def _doc_contains(outer: RetrievedDoc, inner: RetrievedDoc) -> bool:
    """True when `inner`'s time range sits entirely within `outer`'s (same lecture)."""
    return (
        outer.lecture_id == inner.lecture_id
        and outer.start_sec is not None
        and outer.end_sec is not None
        and inner.start_sec is not None
        and inner.end_sec is not None
        and inner.start_sec >= outer.start_sec
        and inner.end_sec <= outer.end_sec
    )


def _merge_recent(
    recent_docs: list[RetrievedDoc], retrieved_docs: list[RetrievedDoc]
) -> list[RetrievedDoc]:
    """Merge the recent-window anchor with retrieval results, removing redundancy
    so the same passage isn't cited twice.

    - If a retrieved doc already *covers* the recent window (e.g. near the start
      of a lecture, a chunk spans 0:00–0:46 while the window is 0:00–0:08), the
      anchor would just duplicate it — drop the anchor, keep the retrieved docs.
    - Otherwise prepend the anchor (so it becomes citation [1] — "currently
      watching") and drop any retrieved doc fully contained in it.

    A doc that only partially overlaps is kept either way (it carries content the
    other doesn't).
    """
    if not recent_docs:
        return list(retrieved_docs)
    rw = recent_docs[0]
    if any(_doc_contains(d, rw) for d in retrieved_docs):
        return list(retrieved_docs)
    kept = [d for d in retrieved_docs if not _doc_contains(rw, d)]
    return [rw, *kept]


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
        # Per-role LMs, built lazily and cached. Empty/unused when an LM is
        # injected (tests pass a DummyLM via `lm=`, which overrides all roles).
        self._lms: dict[str, dspy.LM] = {}
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
            self._lm = build_lm(
                settings,
                temperature=float(settings.chat_temperature),
                max_tokens=_DEFAULT_MAX_TOKENS,
            )
        return self._lm

    def _lm_for(self, role: str) -> dspy.LM:
        """Return the LM for a pipeline role, tiered per `model_roles.ROLE_TIERS`.

        An injected LM (e.g. a `DummyLM` in tests) overrides every role. Built
        LMs are cached per role for the life of the process singleton.
        """
        if self._lm is not None:
            return self._lm
        cached = self._lms.get(role)
        if cached is None:
            settings: Settings = get_settings()
            cached = build_lm_for_role(
                settings,
                role,
                temperature=float(settings.chat_temperature),
                max_tokens=_ROLE_MAX_TOKENS.get(role, _DEFAULT_MAX_TOKENS),
            )
            self._lms[role] = cached
        return cached

    def _record_models(self, debug: dict[str, Any], *roles: str) -> None:
        """Record the resolved model id per role into `debug["models"]`.

        No-op when an LM is injected (tests) — the injected LM, not the
        configured tier, is what runs, so the resolved id would be misleading.
        """
        if self._lm is not None:
            return
        settings: Settings = get_settings()
        models = debug.setdefault("models", {})
        for role in roles:
            models[role] = resolved_model_id(settings, role)

    async def _recent_window(
        self,
        *,
        db: AsyncSession,
        course_info: CourseInfo,
        viewing: ViewingContext | None,
    ) -> list[RetrievedDoc]:
        """The just-watched transcript window as a 0- or 1-element doc list.

        Empty when not in video mode, when the player sent no playback position,
        or when the window holds no transcript (see `retrieve_recent_window`).
        """
        if viewing is None:
            return []
        return await retrieve_recent_window(
            db=db,
            course_info=course_info,
            lecture_slug=viewing.lecture_slug,
            head_sec=viewing.timestamp_sec,
        )

    async def aforward(
        self,
        *,
        db: AsyncSession,
        course_info: CourseInfo,
        conversation_history: ConversationHistory,
        user_query: str,
        viewing: ViewingContext | None = None,
    ) -> TeachingAssistantResult:
        uq = (user_query or "").strip()
        if not uq:
            raise ValueError("user_query must be non-empty")

        # Each signature gets a purpose-built projection of the course (see the
        # `CourseInfo.to_*` views). The router/clarifier/no-context fallback get
        # the lean catalog; query generation gets the rich summaries that drive
        # lecture routing; answer-from-context gets only the header (its content
        # comes from the retrieved docs, and a richer view would invite uncited
        # claims). Rendered lazily so we only pay for the views a branch uses.
        #
        # In video mode (`viewing` set) a one-line anchor is prefixed to every
        # view so signatures can resolve deictic references to the watched
        # lecture/position.
        note = _viewing_note(viewing)
        ci_basic = _view(course_info.to_basic_info(), note)
        ch_str = conversation_history.to_prompt_string()

        debug: dict[str, Any] = {}

        route_pred = await asyncio.to_thread(
            self._route_raw,
            course_info=ci_basic,
            conversation_history=ch_str,
            user_query=uq,
        )
        route = _normalize_route(getattr(route_pred, "route", ""))
        _stash_reasoning(debug, "router_reasoning", route_pred)
        self._record_models(debug, ROUTER)

        if route == "clarify":
            self._record_models(debug, CLARIFY)
            answer = await asyncio.to_thread(
                self._clarify,
                course_info=ci_basic,
                conversation_history=ch_str,
                user_query=uq,
            )
            return TeachingAssistantResult(answer=answer, route="clarify", debug=debug)

        if route == "answer":
            self._record_models(debug, ANSWER_NO_CTX)
            answer = await asyncio.to_thread(
                self._answer_no_ctx,
                course_info=ci_basic,
                conversation_history=ch_str,
                user_query=uq,
            )
            return TeachingAssistantResult(answer=answer, route="answer", debug=debug)

        # route == "retrieve"
        self._record_models(debug, GEN_RETRIEVAL)
        params = await asyncio.to_thread(
            self._gen_retrieval_params,
            course_info=ci_basic,
            conversation_history=ch_str,
            user_query=uq,
        )
        _stash_reasoning(debug, "query_gen_reasoning", params)

        decision = await self._retriever.retrieve(
            db=db,
            course_info=course_info,
            contextualized_query=(str(params.contextualized_query or "").strip() or uq),
            lecture_routing=_apply_soft_scope(list(params.lecture_routing or []), viewing),
            target_lecture_slug=params.target_lecture_slug,
            target_timestamp=params.target_timestamp,
        )

        # Video mode: prepend the just-watched transcript window as a citable
        # anchor. It's additive — merged with (not a replacement for) whatever
        # the cascade retrieved, and can stand alone when retrieval came back
        # empty (so a deictic question still gets answered from on-screen text).
        recent_docs = await self._recent_window(db=db, course_info=course_info, viewing=viewing)
        docs = _merge_recent(recent_docs, list(decision.docs))
        if viewing is not None:
            debug["video_mode"] = True
            debug["recent_window_attached"] = bool(recent_docs)

        if not docs:
            # Terminal "no context" branch: tell the model retrieval failed so
            # it answers honestly instead of hallucinating from missing context.
            primed_query = (
                f"{uq}\n\n"
                "(System note: a retrieval lookup for specific details in the "
                "lecture transcripts returned nothing relevant. Answer carefully "
                "and tell the student what information you don't have access to.)"
            )
            self._record_models(debug, ANSWER_NO_CTX)
            answer = await asyncio.to_thread(
                self._answer_no_ctx,
                course_info=ci_basic,
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
            d.to_prompt_string(index=i) for i, d in enumerate(docs, start=1)
        ]
        self._record_models(debug, ANSWER_WITH_CTX)
        answer = await asyncio.to_thread(
            self._answer_with_ctx,
            course_info=_view(course_info.to_header(), note),
            conversation_history=ch_str,
            user_query=uq,
            retrieved_docs=rendered_docs,
        )
        return TeachingAssistantResult(
            answer=answer,
            route="retrieve",
            # When retrieval was empty and only the recent window remains, label
            # the path 'explicit' (deterministic timestamp retrieval); otherwise
            # keep the cascade's path.
            retrieval_path=decision.path if decision.docs else "explicit",
            retrieved_docs=docs,
            debug=debug,
        )

    async def astream(
        self,
        *,
        db: AsyncSession,
        course_info: CourseInfo,
        conversation_history: ConversationHistory,
        user_query: str,
        viewing: ViewingContext | None = None,
    ) -> AsyncIterator[Event]:
        """Streaming sibling of `aforward`: walks the same cascade but yields
        typed events as it goes, token-streaming **every** LLM call.

        Mirrors `aforward`'s routing exactly (same views, same no-context
        fallback). Every step runs through `dspy.streamify`: the router and
        query-generator stream their `reasoning` as `ThinkingEvent`s (so the
        thinking panel fills token-by-token), and all three answer-producing
        branches (answer-from-context, no-context fallback, general answer,
        clarify) stream their `answer`/`clarification` as `AnswerEvent`s.

        The terminal `DoneEvent` carries the full answer + the metadata the
        endpoint needs to post-process citations and persist. Errors propagate;
        the streaming endpoint (M3) owns the try/except, fallback, persistence.
        """
        uq = (user_query or "").strip()
        if not uq:
            raise ValueError("user_query must be non-empty")

        note = _viewing_note(viewing)
        ci_basic = _view(course_info.to_basic_info(), note)
        ch_str = conversation_history.to_prompt_string()
        debug: dict[str, Any] = {}
        # Reasoning is streamed from up to three sources (router → query-gen →
        # answer). Insert a blank-line separator between blocks so the last
        # sentence of one doesn't run into the first of the next.
        thinking_seen = False

        # --- Router (stream its reasoning as thinking) ---
        route_pred = None
        async for field, payload in self._astream_predict(
            self.router,
            lm=self._lm_for(ROUTER),
            listen_fields=["reasoning"],
            course_info=ci_basic,
            conversation_history=ch_str,
            user_query=uq,
        ):
            if field is None:
                route_pred = payload
            elif field == "reasoning":
                yield ThinkingEvent(delta=payload)
                thinking_seen = True
        route = _normalize_route(getattr(route_pred, "route", ""))
        _stash_reasoning(debug, "router_reasoning", route_pred)
        self._record_models(debug, ROUTER)

        # --- clarify / general-answer routes: stream the reply, no retrieval ---
        if route in ("clarify", "answer"):
            predictor = self.clarifier if route == "clarify" else self.answer_without_context
            answer_field = "clarification" if route == "clarify" else "answer"
            role = CLARIFY if route == "clarify" else ANSWER_NO_CTX
            self._record_models(debug, role)
            out: dict[str, str] = {}
            async for ev in self._emit_answer(
                predictor,
                lm=self._lm_for(role),
                answer_field=answer_field,
                out=out,
                thinking_separator="\n\n" if thinking_seen else "",
                course_info=ci_basic,
                conversation_history=ch_str,
                user_query=uq,
            ):
                yield ev
            yield DoneEvent(answer=out.get("answer", ""), route=route, debug=debug)
            return

        # --- retrieve route ---
        yield StatusEvent(stage="searching", label="Searching course materials…")
        self._record_models(debug, GEN_RETRIEVAL)
        params = None
        qg_first = True
        async for field, payload in self._astream_predict(
            self.query_generator,
            lm=self._lm_for(GEN_RETRIEVAL),
            listen_fields=["reasoning"],
            course_info=ci_basic,
            conversation_history=ch_str,
            user_query=uq,
        ):
            if field is None:
                params = payload
            elif field == "reasoning":
                if qg_first and thinking_seen:
                    yield ThinkingEvent(delta="\n\n")
                qg_first = False
                yield ThinkingEvent(delta=payload)
                thinking_seen = True
        _stash_reasoning(debug, "query_gen_reasoning", params)

        decision = await self._retriever.retrieve(
            db=db,
            course_info=course_info,
            contextualized_query=(str(params.contextualized_query or "").strip() or uq),
            lecture_routing=_apply_soft_scope(list(params.lecture_routing or []), viewing),
            target_lecture_slug=params.target_lecture_slug,
            target_timestamp=params.target_timestamp,
        )

        # Video mode: prepend the just-watched window (see `aforward`).
        recent_docs = await self._recent_window(db=db, course_info=course_info, viewing=viewing)
        docs = _merge_recent(recent_docs, list(decision.docs))
        if viewing is not None:
            debug["video_mode"] = True
            debug["recent_window_attached"] = bool(recent_docs)

        if not docs:
            # Terminal "no context" branch: prime an honest answer, still streamed.
            primed_query = (
                f"{uq}\n\n"
                "(System note: a retrieval lookup for specific details in the "
                "lecture transcripts returned nothing relevant. Answer carefully "
                "and tell the student what information you don't have access to.)"
            )
            self._record_models(debug, ANSWER_NO_CTX)
            out = {}
            async for ev in self._emit_answer(
                self.answer_without_context,
                lm=self._lm_for(ANSWER_NO_CTX),
                answer_field="answer",
                out=out,
                thinking_separator="\n\n" if thinking_seen else "",
                course_info=ci_basic,
                conversation_history=ch_str,
                user_query=primed_query,
            ):
                yield ev
            yield DoneEvent(
                answer=out.get("answer", ""),
                route="retrieve",
                retrieval_path="none",
                retrieved_docs=[],
                debug=debug,
            )
            return

        # Sources first, so the client can render them while the answer streams.
        yield CitationsEvent(docs=list(docs))
        yield StatusEvent(stage="generating", label=None)

        rendered_docs = [
            d.to_prompt_string(index=i) for i, d in enumerate(docs, start=1)
        ]
        self._record_models(debug, ANSWER_WITH_CTX)
        out = {}
        async for ev in self._emit_answer(
            self.answer_from_context,
            lm=self._lm_for(ANSWER_WITH_CTX),
            answer_field="answer",
            out=out,
            thinking_separator="\n\n" if thinking_seen else "",
            course_info=_view(course_info.to_header(), note),
            conversation_history=ch_str,
            user_query=uq,
            retrieved_docs=rendered_docs,
        ):
            yield ev
        yield DoneEvent(
            answer=out.get("answer", ""),
            route="retrieve",
            retrieval_path=decision.path if decision.docs else "explicit",
            retrieved_docs=list(docs),
            debug=debug,
        )

    async def _astream_predict(
        self,
        predictor: dspy.Module,
        *,
        lm: dspy.LM,
        listen_fields: list[str],
        **inputs: Any,
    ) -> AsyncIterator[tuple[str | None, Any]]:
        """Run a predictor via `dspy.streamify`, yielding `(field_name, delta)`
        for each streamed chunk of a listened output field, then `(None, prediction)`
        once with the final `dspy.Prediction`.

        Wrapper + listeners are built per call: the module is a process singleton
        and `StreamListener` carries per-run state, so reusing instances across
        turns would bleed state. Runs on the event loop (litellm async streaming),
        not via `to_thread`.
        """
        listeners = [
            dspy.streaming.StreamListener(signature_field_name=f) for f in listen_fields
        ]
        streamed = dspy.streamify(
            predictor, stream_listeners=listeners, async_streaming=True
        )
        final: Any = None
        with dspy.settings.context(lm=lm):
            async for chunk in streamed(**inputs):
                if isinstance(chunk, dspy.streaming.StreamResponse):
                    yield (chunk.signature_field_name, str(chunk.chunk or ""))
                elif isinstance(chunk, dspy.Prediction):
                    final = chunk
        yield (None, final)

    async def _emit_answer(
        self,
        predictor: dspy.Module,
        *,
        lm: dspy.LM,
        answer_field: str,
        out: dict[str, str],
        thinking_separator: str = "",
        **inputs: Any,
    ) -> AsyncIterator[Event]:
        """Stream one answer-producing predictor: emit `ThinkingEvent`s for its
        `reasoning` (when present) and `AnswerEvent`s for its answer field
        (`answer` or `clarification`). Writes the full answer text to `out["answer"]`.

        `thinking_separator`, when set, is emitted once before this predictor's
        first reasoning chunk — used by `astream` to put a blank line between this
        block and the preceding router/query-gen reasoning. Only emitted if the
        predictor actually streams reasoning (so it never leaves a trailing gap).

        Falls back to one whole-answer `AnswerEvent` when the provider returned no
        incremental answer chunks (granularity is nondeterministic) — so the client
        always receives the answer, streamed or not.
        """
        listen = [answer_field] if answer_field == "clarification" else ["reasoning", answer_field]
        parts: list[str] = []
        final: Any = None
        answer_streamed = False
        reasoning_seen = False
        async for field, payload in self._astream_predict(
            predictor, lm=lm, listen_fields=listen, **inputs
        ):
            if field is None:
                final = payload
            elif field == "reasoning":
                if not reasoning_seen and thinking_separator:
                    yield ThinkingEvent(delta=thinking_separator)
                reasoning_seen = True
                yield ThinkingEvent(delta=payload)
            elif field == answer_field:
                parts.append(payload)
                answer_streamed = True
                yield AnswerEvent(delta=payload)

        text = (str(getattr(final, answer_field, "") or "") or "".join(parts)).strip()
        if not answer_streamed and text:
            yield AnswerEvent(delta=text)
        out["answer"] = text

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
        with dspy.settings.context(lm=self._lm_for(ROUTER)):
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
        with dspy.settings.context(lm=self._lm_for(CLARIFY)):
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
        with dspy.settings.context(lm=self._lm_for(ANSWER_NO_CTX)):
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
        with dspy.settings.context(lm=self._lm_for(GEN_RETRIEVAL)):
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
        with dspy.settings.context(lm=self._lm_for(ANSWER_WITH_CTX)):
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
