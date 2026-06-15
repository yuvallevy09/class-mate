"""Experimental v2 chat endpoint backed by the new `TeachingAssistant` pipeline.

Lives alongside `course_chat` (v1) so we can validate the routed cascade on
real traffic without risk. The frontend opts in by hitting `/chat-v2`. Once
this proves itself we delete the v1 endpoint and the legacy `ChatEngine`
machinery.

Pipeline:

    auth -> ensure_owned_course
         -> conversation lookup/create
         -> build conversation history (BEFORE persisting user message)
         -> persist user message
         -> (optional) generate conversation title for new chats
         -> [isolated read session]
              build_course_info_cached
              TeachingAssistant.aforward
         -> map RetrievedDoc -> ChatCitation
         -> attach URLs + chapter titles (existing helpers)
         -> format inline citation links (existing helper)
         -> persist assistant message
         -> structured log line
         -> return CourseChatResponse

Notes:

- We use a separate AsyncSession for the TeachingAssistant call (mirrors the
  pattern in `course_chat` v1). Retrieval can raise Postgres errors that
  abort the current transaction; isolating it keeps the write transaction
  used for message persistence clean.

- All `RetrievedDoc`s in this pipeline are video-scoped (the cascade filters
  hybrid hits by `video_asset_id`), so the `extra.type='video'` shape is
  always correct here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chat_engine import ChatEngine
from app.ai.stream_events import (
    AnswerEvent,
    CitationsEvent,
    DoneEvent,
    Event,
    StatusEvent,
    ThinkingEvent,
)
from app.ai.teaching_assistant import TeachingAssistant, get_teaching_assistant
from app.api.deps import get_current_user
from app.api.v1.chat import (
    _attach_citation_urls,
    _attach_video_chapter_titles,
    _ensure_owned_course,
    _format_reply_with_citation_links,
)
from app.core.settings import Settings, get_settings
from app.db.models.chat_conversation import ChatConversation
from app.db.models.chat_message import ChatMessage
from app.db.models.course import Course
from app.db.models.video_asset import VideoAsset
from app.db.session import get_db, get_session_maker
from app.schemas.chat import ChatCitation, CourseChatRequest, CourseChatResponse
from app.schemas.course_info import CourseInfo
from app.schemas.retrieval import RetrievedDoc
from app.schemas.viewing_context import ViewingContext
from app.services.conversation_history import build_conversation_history
from app.services.course_info import build_course_info_cached
from app.schemas.conversation_history import ConversationHistory

router = APIRouter(tags=["chat-v2"])
logger = logging.getLogger(__name__)


_LLM_ERROR_FALLBACK = (
    "I couldn’t reach the language model right now. "
    "Please retry in a moment — if this keeps happening, check your API key/quota and server logs."
)


def _build_thinking(
    *,
    route: str | None,
    retrieval_path: str | None,
    retrieved_docs: list[RetrievedDoc],
    debug: dict,
) -> str | None:
    """Compose a plain-text "thinking" explanation for the live turn.

    Built entirely from data the cascade already produced:
    - `debug` carries the `ChainOfThought` reasoning strings (`router_reasoning`,
      `query_gen_reasoning`) that `TeachingAssistant` stashes.
    - On the retrieve path we prepend a one-line summary of where we looked and
      how much we found, derived from `retrieval_path` + the retrieved doc slugs.

    Returns `None` when there's nothing substantive — the UI then hides the
    reasoning panel (and still shows the "Thought for Ns" line). Reasoning text
    is the model's raw rationale; surfaced as-is for now.
    """
    router_reasoning = str(debug.get("router_reasoning") or "").strip()
    query_gen_reasoning = str(debug.get("query_gen_reasoning") or "").strip()

    parts: list[str] = []
    if route == "retrieve":
        # Distinct lecture slugs in first-seen order, for a readable summary.
        slugs: list[str] = []
        for d in retrieved_docs:
            if d.lecture_slug and d.lecture_slug not in slugs:
                slugs.append(d.lecture_slug)
        if retrieval_path == "none" or not retrieved_docs:
            parts.append("Looked through the course materials but found nothing relevant.")
        elif slugs:
            where = ", ".join(slugs)
            n = len(retrieved_docs)
            passages = "passage" if n == 1 else "passages"
            parts.append(f"Searched lectures {where} — found {n} relevant {passages}.")
        if query_gen_reasoning:
            parts.append(query_gen_reasoning)
    elif router_reasoning:
        # answer / clarify: the router's rationale is the only thinking we have.
        parts.append(router_reasoning)

    text = "\n\n".join(p for p in parts if p).strip()
    return text or None


async def _validated_video_asset_id(
    *,
    db: AsyncSession,
    course_id: UUID,
    watching_video_asset_id: UUID | None,
) -> UUID | None:
    """Return the watched asset id when it belongs to this course, else None.

    Used to tag a newly created conversation with its lecture (so per-video
    history can be listed later). Course-ownership is enforced — not
    transcript-readiness: a conversation belongs to its lecture even before the
    transcript is ingested. Readiness only gates retrieval (`ViewingContext`).
    """
    if watching_video_asset_id is None:
        return None
    res = await db.execute(
        select(VideoAsset.id).where(
            VideoAsset.id == watching_video_asset_id,
            VideoAsset.course_id == course_id,
        )
    )
    return res.scalar_one_or_none()


def _resolve_viewing_context(
    *,
    course_info: CourseInfo,
    watching_video_asset_id: UUID | None,
    watching_timestamp_sec: float | None,
) -> ViewingContext | None:
    """Turn the player's `watching_*` request fields into a `ViewingContext`.

    Graceful degrade — returns None (→ ordinary course chat) when no asset was
    sent, the asset isn't part of this course, or its transcript isn't ingested
    yet (the readiness check lives in `ViewingContext.from_lecture`). Never
    raises: a stale/invalid watched id quietly disables video mode rather than
    failing the turn.
    """
    if watching_video_asset_id is None:
        return None
    lecture = course_info.lecture_by_id(watching_video_asset_id)
    if lecture is None:
        return None
    return ViewingContext.from_lecture(lecture, timestamp_sec=watching_timestamp_sec)


# Inline transcript markers like "[0:00] " / "[1:02:15] " that the retriever
# renders into doc text (to help the model cite). They're for the model, not the
# reader — stripped from the user-facing citation snippet.
_TS_MARKER_RE = re.compile(r"\[\d{1,3}:\d{2}(?::\d{2})?\]\s*")


def _docs_to_citations(docs: list[RetrievedDoc]) -> list[ChatCitation]:
    """Map retrieved docs into the `ChatCitation` shape the frontend already renders.

    The `extra` dict mirrors what `ChatEngine` emits in v1 so the existing
    `_attach_citation_urls` / `_attach_video_chapter_titles` /
    `_format_reply_with_citation_links` helpers work unchanged. We also stash
    `lectureSlug` so `_normalize_slug_citations` can recover when the LLM
    cites by slug (`[L1]`) instead of by number (`[1]`).
    """
    out: list[ChatCitation] = []
    for d in docs:
        extra: dict = {"type": "video", "lectureSlug": d.lecture_slug}
        if d.start_sec is not None:
            extra["startSec"] = float(d.start_sec)
        if d.end_sec is not None:
            extra["endSec"] = float(d.end_sec)
        if d.chapter_id is not None:
            extra["chapterId"] = str(d.chapter_id)
        if d.chapter_title:
            extra["chapterTitle"] = d.chapter_title

        snippet = _TS_MARKER_RE.sub("", d.text or "").strip()
        if len(snippet) > 240:
            snippet = snippet[:240].rstrip() + "…"

        out.append(
            ChatCitation(
                content_id=d.content_id,
                title=d.lecture_title or d.lecture_slug,
                snippet=snippet or None,
                extra=extra,
            )
        )
    return out


# Match anything between brackets that isn't already a pure-digit citation
# the downstream regex handles. Length cap keeps us from eating big inline
# markdown like `[click here](url)` (those have `]` followed by `(`, but the
# cap is still useful defense). Note: the existing `_format_reply_with_citation_links`
# regex runs AFTER this and only touches `[<digits>]`, so we deliberately
# rewrite slug citations into the canonical `[N]` form.
_SLUG_CITATION_RE = re.compile(r"\[([^\]\n]{1,30})\]")


def _normalize_slug_citations(reply: str, citations: list[ChatCitation]) -> str:
    """Recover `[L1]`-style slug citations into the canonical `[N]` form.

    The `AnswerFromContext` prompt is explicit that citations must be numeric
    (`[1]`, `[2]`), but smaller / faster models still occasionally echo back
    the lecture slug they see in `course_info` (`[L1]`). The digit-only regex
    in `_format_reply_with_citation_links` would leave those as plain text.

    This pass walks every `[...]` in the reply and, if the inside text matches
    a lecture slug we actually retrieved (case-insensitive, with an optional
    'Lecture ' prefix tolerated), rewrites it to `[N]` where N is the 1-based
    index of the first matching citation. Pure-digit brackets are left
    untouched. Unknown tokens are left untouched.
    """
    if not citations or not reply:
        return reply

    slug_to_index: dict[str, int] = {}
    for i, c in enumerate(citations, start=1):
        slug = (c.extra or {}).get("lectureSlug")
        if isinstance(slug, str):
            k = slug.strip().upper()
            if k:
                slug_to_index.setdefault(k, i)

    if not slug_to_index:
        return reply

    def _repl(m: re.Match) -> str:
        inside = m.group(1).strip()
        if not inside or inside.isdigit():
            return m.group(0)
        normalized = re.sub(r"^lecture\s+", "", inside, flags=re.IGNORECASE).strip().upper()
        idx = slug_to_index.get(normalized)
        if idx is None:
            return m.group(0)
        return f"[{idx}]"

    return _SLUG_CITATION_RE.sub(_repl, reply)


async def _maybe_generate_title(
    *,
    settings: Settings,
    conversation: ChatConversation,
    first_user_message: str,
    course_name: str,
) -> None:
    """Best-effort: name a brand-new conversation from its first user message.

    Reuses the existing `ChatEngine.generate_title` so we don't fork another
    DSPy module just for titling. Silently no-ops on failure — the UI falls
    back to "Conversation".
    """
    if conversation.title:
        return
    try:
        engine = ChatEngine(settings)
        title = await engine.generate_title(
            course_name=course_name,
            first_user_message=first_user_message,
        )
        if title:
            conversation.title = title
    except Exception:
        pass


@router.post("/courses/{course_id}/chat-v2", response_model=CourseChatResponse)
async def course_chat_v2(
    course_id: UUID,
    body: CourseChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    ta: TeachingAssistant = Depends(get_teaching_assistant),
) -> CourseChatResponse:
    course = await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    # --- Conversation lookup / create ---
    conversation: ChatConversation | None = None
    created_new_conversation = False
    if body.conversation_id:
        try:
            convo_id = UUID(str(body.conversation_id))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid conversationId",
            )

        res = await db.execute(
            select(ChatConversation).where(
                ChatConversation.id == convo_id,
                ChatConversation.course_id == course.id,
            )
        )
        conversation = res.scalar_one_or_none()
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )

    if conversation is None:
        # Tag the conversation with the watched lecture when started from the
        # video player, so it can be listed per-video later (NULL = course chat).
        new_video_asset_id = await _validated_video_asset_id(
            db=db, course_id=course.id, watching_video_asset_id=body.watching_video_asset_id
        )
        conversation = ChatConversation(
            course_id=course.id, title=None, video_asset_id=new_video_asset_id
        )
        db.add(conversation)
        await db.flush()  # surface conversation.id
        created_new_conversation = True

    # --- Load history BEFORE persisting the new user message ---
    # (Mirrors v1: keeps the current message from leaking into its own history.)
    max_n = int(settings.chat_history_max_messages)
    history = await build_conversation_history(
        db=db, conversation_id=conversation.id, max_n=max_n
    )

    # --- Persist user message early so it survives any LLM error ---
    db.add(ChatMessage(conversation_id=conversation.id, role="user", content=body.message))

    # --- Best-effort title generation for new conversations ---
    if created_new_conversation:
        await _maybe_generate_title(
            settings=settings,
            conversation=conversation,
            first_user_message=body.message,
            course_name=str(course.name),
        )

    # --- Run TeachingAssistant in an isolated session ---
    # Retrieval can raise Postgres errors that abort the current transaction;
    # using a fresh session protects the write transaction above.
    answer_text = _LLM_ERROR_FALLBACK
    route: str | None = None
    retrieval_path: str | None = None
    retrieved_docs: list[RetrievedDoc] = []
    debug: dict = {}
    latency_ms: int = 0
    error: str | None = None

    t0 = time.perf_counter()
    try:
        SessionLocal = get_session_maker()
        async with SessionLocal() as ta_db:
            course_info = await build_course_info_cached(db=ta_db, course=course)
            viewing = _resolve_viewing_context(
                course_info=course_info,
                watching_video_asset_id=body.watching_video_asset_id,
                watching_timestamp_sec=body.watching_timestamp_sec,
            )
            result = await ta.aforward(
                db=ta_db,
                course_info=course_info,
                conversation_history=history,
                user_query=body.message,
                viewing=viewing,
            )
        answer_text = result.answer or _LLM_ERROR_FALLBACK
        route = result.route
        retrieval_path = result.retrieval_path
        retrieved_docs = list(result.retrieved_docs)
        debug = dict(result.debug or {})
    except Exception as e:
        # Any failure inside the cascade — LM parse errors, DB hiccups, etc. —
        # gets persisted as a visible assistant message so the conversation
        # doesn't look "stuck" and the user message we already added isn't
        # left dangling. Use `logger.exception` to capture the traceback;
        # `ValueError` is intentionally NOT special-cased because pydantic
        # validation inside DSPy raises it for LM output mismatches (not user
        # input problems).
        logger.exception("ta.aforward failed (persisting error reply)")
        error = type(e).__name__
    finally:
        latency_ms = int((time.perf_counter() - t0) * 1000)

    # --- Citations: map -> enrich -> inline links ---
    citations = _docs_to_citations(retrieved_docs)
    citations = await _attach_citation_urls(
        db=db, settings=settings, course_id=course.id, citations=citations
    )
    citations = await _attach_video_chapter_titles(
        db=db, course_id=course.id, citations=citations
    )
    # Defensive: recover `[L1]`-style slug citations into `[N]` form before
    # the digit-only post-processor builds the in-page links.
    normalized_answer = _normalize_slug_citations(answer_text, citations)
    reply_with_links = _format_reply_with_citation_links(normalized_answer, citations)

    # Computed once: persisted with the message AND returned for the live turn.
    thinking = _build_thinking(
        route=route,
        retrieval_path=retrieval_path,
        retrieved_docs=retrieved_docs,
        debug=debug,
    )

    # --- Persist assistant message + bump conversation activity ---
    # Thinking is a live-only affordance (returned below for the current turn)
    # and intentionally NOT persisted — no reasoning panel on reload.
    db.add(
        ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=reply_with_links,
            citations=[c.model_dump(mode="json") for c in citations] if citations else None,
            thinking=None,
        )
    )
    conversation.last_message_at = datetime.now(timezone.utc)
    await db.commit()

    # --- Structured log: route + retrieval path + latency, query only on failures ---
    logger.info(
        "ta_turn",
        extra={
            "course_id": str(course.id),
            "conversation_id": str(conversation.id),
            "route": route,
            "retrieval_path": retrieval_path,
            "docs_count": len(retrieved_docs),
            "doc_slugs": [d.lecture_slug for d in retrieved_docs],
            "latency_ms": latency_ms,
            "error": error,
            # Privacy-friendly default: only log the query when something went wrong.
            "user_query": body.message if (error or retrieval_path == "none") else None,
        },
    )

    return CourseChatResponse(
        text=reply_with_links,
        citations=citations,
        conversation_id=conversation.id,
        thinking=thinking,
    )


# --- SSE streaming endpoint (M3) ---------------------------------------------
#
# Streams `TeachingAssistant.astream` events as Server-Sent Events, then persists
# the assistant message — the streaming sibling of `course_chat_v2`. The non-
# streaming endpoint above stays as the fallback.


def _sse_frame(payload: dict) -> str:
    """Serialize one event dict as an SSE `data:` frame."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event_to_payload(ev: Event) -> dict | None:
    """Map a typed stream event to its wire-JSON dict.

    Returns `None` for `CitationsEvent` and `DoneEvent`: those are handled inline
    in the generator (citations need async DB enrichment; done is emitted only
    after persistence).
    """
    if isinstance(ev, StatusEvent):
        return {"type": "status", "stage": ev.stage, "label": ev.label}
    if isinstance(ev, ThinkingEvent):
        return {"type": "thinking", "delta": ev.delta}
    if isinstance(ev, AnswerEvent):
        return {"type": "answer", "delta": ev.delta}
    return None


async def _persist_assistant_message(
    *,
    db: AsyncSession,
    conversation_id: UUID,
    answer: str,
    citations: list[ChatCitation],
) -> str:
    """Format inline citation links, persist the assistant message, and bump
    conversation activity. Shared by the streaming endpoint's success, error,
    and disconnect paths. Returns the stored `reply_with_links` so the `done`
    frame can hand the client the exact persisted text (which it normalizes to
    match `turn.answer` for the live→persisted handoff).

    Thinking is intentionally NOT persisted: the thought process is a live-only
    affordance (streamed during the turn), so the saved message has `thinking`
    NULL and no reasoning panel appears on reload.

    `citations` is pre-enriched by the caller (the generator enriches once, at
    the `CitationsEvent`, to send them before the answer).
    """
    normalized = _normalize_slug_citations(answer, citations)
    reply_with_links = _format_reply_with_citation_links(normalized, citations)
    db.add(
        ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=reply_with_links,
            citations=[c.model_dump(mode="json") for c in citations] if citations else None,
            thinking=None,
        )
    )
    res = await db.execute(
        select(ChatConversation).where(ChatConversation.id == conversation_id)
    )
    convo = res.scalar_one_or_none()
    if convo is not None:
        convo.last_message_at = datetime.now(timezone.utc)
    await db.commit()
    return reply_with_links


@router.post("/courses/{course_id}/chat-v2/stream")
async def course_chat_v2_stream(
    course_id: UUID,
    body: CourseChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    ta: TeachingAssistant = Depends(get_teaching_assistant),
) -> StreamingResponse:
    # --- Pre-stream work on the request session (closes before the generator
    # runs). All HTTPExceptions fire here, as normal JSON errors. ---
    course = await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    conversation: ChatConversation | None = None
    created_new_conversation = False
    if body.conversation_id:
        try:
            convo_id = UUID(str(body.conversation_id))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid conversationId"
            )
        res = await db.execute(
            select(ChatConversation).where(
                ChatConversation.id == convo_id,
                ChatConversation.course_id == course.id,
            )
        )
        conversation = res.scalar_one_or_none()
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

    if conversation is None:
        # Tag with the watched lecture (NULL = course chat); see non-streaming.
        new_video_asset_id = await _validated_video_asset_id(
            db=db, course_id=course.id, watching_video_asset_id=body.watching_video_asset_id
        )
        conversation = ChatConversation(
            course_id=course.id, title=None, video_asset_id=new_video_asset_id
        )
        db.add(conversation)
        await db.flush()
        created_new_conversation = True

    max_n = int(settings.chat_history_max_messages)
    history = await build_conversation_history(
        db=db, conversation_id=conversation.id, max_n=max_n
    )
    db.add(ChatMessage(conversation_id=conversation.id, role="user", content=body.message))
    if created_new_conversation:
        await _maybe_generate_title(
            settings=settings,
            conversation=conversation,
            first_user_message=body.message,
            course_name=str(course.name),
        )
    await db.commit()

    # Capture plain values for the generator — the request session `db` and any
    # ORM objects bound to it are unusable once we return the StreamingResponse.
    captured_course_id: UUID = course.id
    captured_conversation_id: UUID = conversation.id
    captured_history: ConversationHistory = history
    user_query: str = body.message
    captured_watching_asset_id: UUID | None = body.watching_video_asset_id
    captured_watching_timestamp_sec: float | None = body.watching_timestamp_sec

    async def _gen() -> AsyncIterator[str]:
        SessionLocal = get_session_maker()
        enriched_citations: list[ChatCitation] = []
        answer_parts: list[str] = []
        done: DoneEvent | None = None
        try:
            # Retrieval session (isolated; can raise Postgres errors).
            async with SessionLocal() as ta_db:
                res = await ta_db.execute(select(Course).where(Course.id == captured_course_id))
                gen_course = res.scalar_one()  # ownership already verified above
                course_info = await build_course_info_cached(db=ta_db, course=gen_course)
                viewing = _resolve_viewing_context(
                    course_info=course_info,
                    watching_video_asset_id=captured_watching_asset_id,
                    watching_timestamp_sec=captured_watching_timestamp_sec,
                )
                async for ev in ta.astream(
                    db=ta_db,
                    course_info=course_info,
                    conversation_history=captured_history,
                    user_query=user_query,
                    viewing=viewing,
                ):
                    if isinstance(ev, CitationsEvent):
                        # Enrich on a fresh session, then emit BEFORE answer
                        # deltas (astream guarantees Citations precedes Answer).
                        cites = _docs_to_citations(ev.docs)
                        async with SessionLocal() as enrich_db:
                            cites = await _attach_citation_urls(
                                db=enrich_db, settings=settings,
                                course_id=captured_course_id, citations=cites,
                            )
                            cites = await _attach_video_chapter_titles(
                                db=enrich_db, course_id=captured_course_id, citations=cites,
                            )
                        enriched_citations = cites
                        yield _sse_frame({
                            "type": "citations",
                            "citations": [c.model_dump(mode="json") for c in cites],
                        })
                        continue
                    if isinstance(ev, DoneEvent):
                        done = ev
                        continue
                    # Thinking streams live but is not persisted; only the answer
                    # is accumulated (for the persistence safety net).
                    if isinstance(ev, AnswerEvent):
                        answer_parts.append(ev.delta)
                    payload = _event_to_payload(ev)
                    if payload is not None:
                        yield _sse_frame(payload)

            # Terminal: persist on a fresh write session, then emit `done`.
            answer_text = (
                (done.answer if done else "") or "".join(answer_parts) or _LLM_ERROR_FALLBACK
            )
            async with SessionLocal() as write_db:
                reply_with_links = await _persist_assistant_message(
                    db=write_db,
                    conversation_id=captured_conversation_id,
                    answer=answer_text,
                    citations=enriched_citations,
                )
            logger.info(
                "ta_stream_turn",
                extra={
                    "course_id": str(captured_course_id),
                    "conversation_id": str(captured_conversation_id),
                    "route": (done.route if done else None),
                    "retrieval_path": (done.retrieval_path if done else None),
                    "docs_count": len(done.retrieved_docs) if done else 0,
                },
            )
            yield _sse_frame({
                "type": "done",
                "conversationId": str(captured_conversation_id),
                "text": reply_with_links,
            })

        except asyncio.CancelledError:
            # Client disconnected: best-effort persist so the turn isn't left
            # dangling, then re-raise (never swallow CancelledError).
            await _persist_on_failure(
                SessionLocal, captured_conversation_id, answer_parts, enriched_citations
            )
            raise
        except Exception:
            logger.exception("chat-v2 stream failed (persisting fallback)")
            await _persist_on_failure(
                SessionLocal, captured_conversation_id, answer_parts, enriched_citations
            )
            yield _sse_frame({"type": "error", "message": _LLM_ERROR_FALLBACK})

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _persist_on_failure(
    session_maker,
    conversation_id: UUID,
    answer_parts: list[str],
    citations: list[ChatCitation],
) -> None:
    """Persist a partial-or-fallback assistant message after a mid-stream error
    or disconnect. Swallows its own errors so it can't mask the original."""
    try:
        async with session_maker() as write_db:
            await _persist_assistant_message(
                db=write_db,
                conversation_id=conversation_id,
                answer=("".join(answer_parts) or _LLM_ERROR_FALLBACK),
                citations=citations,
            )
    except Exception:
        logger.exception("chat-v2 stream: failure-path persist failed")
