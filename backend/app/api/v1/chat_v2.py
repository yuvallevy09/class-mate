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

import logging
import re
import time
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chat_engine import ChatEngine
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
from app.db.session import get_db, get_session_maker
from app.schemas.chat import ChatCitation, CourseChatRequest, CourseChatResponse
from app.schemas.retrieval import RetrievedDoc
from app.services.conversation_history import build_conversation_history
from app.services.course_info import build_course_info_cached

router = APIRouter(tags=["chat-v2"])
logger = logging.getLogger(__name__)


_LLM_ERROR_FALLBACK = (
    "I couldn’t reach the language model right now. "
    "Please retry in a moment — if this keeps happening, check your API key/quota and server logs."
)


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

        snippet = (d.text or "").strip()
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
        conversation = ChatConversation(course_id=course.id, title=None)
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
    latency_ms: int = 0
    error: str | None = None

    t0 = time.perf_counter()
    try:
        SessionLocal = get_session_maker()
        async with SessionLocal() as ta_db:
            course_info = await build_course_info_cached(db=ta_db, course=course)
            result = await ta.aforward(
                db=ta_db,
                course_info=course_info,
                conversation_history=history,
                user_query=body.message,
            )
        answer_text = result.answer or _LLM_ERROR_FALLBACK
        route = result.route
        retrieval_path = result.retrieval_path
        retrieved_docs = list(result.retrieved_docs)
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

    # --- Persist assistant message + bump conversation activity ---
    db.add(
        ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=reply_with_links,
            citations=[c.model_dump(mode="json") for c in citations] if citations else None,
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
    )
