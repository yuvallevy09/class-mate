from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chat_engine import ChatEngine, ChatHistoryItem
from app.ai.dspy_router import route_message
from app.api.deps import get_current_user
from app.core.settings import Settings, get_settings
from app.db.models.chat_conversation import ChatConversation
from app.db.models.chat_message import ChatMessage
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.user import User
from app.db.models.video_asset import VideoAsset
from app.db.session import get_db, get_session_maker
from app.rag.hybrid_retrieve import HybridRetrieveConfig, retrieve_course_hybrid_hits
from app.schemas.chat import ChatCitation, CourseChatRequest, CourseChatResponse
from app.schemas.chat_persistence import ChatConversationPublic, ChatMessagePublic

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


class VideoChatHistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=4000)

    @field_validator("content")
    @classmethod
    def _strip_content(cls, v: str) -> str:
        return (v or "").strip()


class VideoChatRequest(BaseModel):
    # For mode="chat", this is the user's question. For mode="summary", it can be empty.
    message: str = Field(default="", max_length=4000)
    history: list[VideoChatHistoryItem] = Field(default_factory=list)
    mode: Literal["chat", "summary"] = "chat"

    # Optional identifiers for video-scoped context.
    content_id: UUID | None = Field(default=None, validation_alias="contentId")
    video_asset_id: UUID | None = Field(default=None, validation_alias="videoAssetId")

    @field_validator("message")
    @classmethod
    def _strip_message(cls, v: str) -> str:
        return (v or "").strip()


class VideoChatResponse(BaseModel):
    text: str


async def _attach_citation_urls(
    *,
    db: AsyncSession,
    settings: Settings,
    course_id: UUID,
    citations: list[ChatCitation],
) -> list[ChatCitation]:
    """Best-effort: attach URLs for citations (when possible).

    - For video transcript citations, prefer a stable in-app VideoPlayer URL (no S3 required).
    - For file-backed citations, prefer a stable in-app API link that redirects to a fresh presigned URL.
    """
    if not citations:
        return citations

    # Video citations: link to the in-app VideoPlayer page at the relevant timestamp.
    # This is stable across sessions and avoids leaking raw presigned URLs into chat history.
    for c in citations:
        extra = c.extra or {}
        if extra.get("type") == "video" and c.content_id and not c.url:
            try:
                start = float(extra.get("startSec") or 0.0)
            except Exception:
                start = 0.0
            # Use an integer second for URL cleanliness.
            t = int(start) if start >= 0 else 0
            c.url = f"/VideoPlayer?courseId={course_id}&contentId={c.content_id}&t={t}"

    if not settings.s3_bucket:
        return citations

    content_ids: list[UUID] = []
    for c in citations:
        if c.content_id and not c.url:
            content_ids.append(c.content_id)
    if not content_ids:
        return citations

    res = await db.execute(
        select(CourseContent.id, CourseContent.file_key).where(
            CourseContent.course_id == course_id,
            CourseContent.id.in_(content_ids),
            CourseContent.file_key.is_not(None),
        )
    )
    has_file_by_id: set[UUID] = {cid for (cid, key) in res.all() if cid and key}
    if not has_file_by_id:
        return citations

    for c in citations:
        if c.content_id and not c.url:
            if c.content_id not in has_file_by_id:
                continue
            # Stable link: API endpoint will redirect to a fresh presigned URL on click.
            c.url = f"/api/v1/contents/{c.content_id}/download-redirect"
    return citations


_CITATION_RE = re.compile(r"\[(?:#\s*)?(\d{1,3})\]")


_SUPERSCRIPT_DIGITS = {
    "0": "⁰",
    "1": "¹",
    "2": "²",
    "3": "³",
    "4": "⁴",
    "5": "⁵",
    "6": "⁶",
    "7": "⁷",
    "8": "⁸",
    "9": "⁹",
}


def _sup_number(n: int) -> str:
    s = str(max(0, int(n)))
    return "".join(_SUPERSCRIPT_DIGITS.get(ch, ch) for ch in s)


def _format_reply_with_citation_links(reply: str, citations: list[ChatCitation]) -> str:
    """
    Convert inline citation markers like [1] or [#1] into in-page links that the frontend
    can use to scroll to a structured "Sources" section rendered from `citations`.

    We intentionally do NOT append sources/footnotes into the message content. This keeps
    chat text clean and avoids persisting brittle markdown footnote internals.
    """
    text = (reply or "").strip()
    if not citations:
        return text

    n = len(citations)

    # Video citations UX:
    # - The footnote number represents the video (we use the first citation index for that video).
    # - A letter represents the time range (a, b, c...) and is shown as a superscript character
    #   adjacent to the footnote marker in the answer.
    # - The footnote definition for the video contains a grouped list of ranges with links that
    #   open the in-app VideoPlayer (frontend opens in a new tab based on title="video:...").
    #
    # This keeps the main answer clean and makes sources more navigable.
    _SUPERSCRIPT_LETTERS = [
        "ᵃ",  # a
        "ᵇ",  # b
        "ᶜ",  # c
        "ᵈ",  # d
        "ᵉ",  # e
        "ᶠ",  # f
        "ᵍ",  # g
        "ʰ",  # h
        "ⁱ",  # i
        "ʲ",  # j
        "ᵏ",  # k
        "ˡ",  # l
        "ᵐ",  # m
        "ⁿ",  # n
        "ᵒ",  # o
        "ᵖ",  # p
        "ʳ",  # r
        "ˢ",  # s
        "ᵗ",  # t
        "ᵘ",  # u
        "ᵛ",  # v
        "ʷ",  # w
        "ˣ",  # x
        "ʸ",  # y
        "ᶻ",  # z
    ]

    # Group video citations by content_id; "number" is the first citation index where it appears.
    video_group_first_index: dict[str, int] = {}
    video_group_items: dict[str, list[tuple[int, float, float, str | None, str | None]]] = {}
    # key -> list of (original_citation_index, start_sec, end_sec, url, title_for_link)

    for i, c in enumerate(citations, start=1):
        extra = c.extra or {}
        if extra.get("type") != "video" or not c.content_id:
            continue
        key = str(c.content_id)
        video_group_first_index[key] = min(video_group_first_index.get(key, i), i)
        try:
            start = float(extra.get("startSec") or 0.0)
        except Exception:
            start = 0.0
        try:
            end = float(extra.get("endSec") or 0.0)
        except Exception:
            end = start
        url = str(c.url) if c.url else None
        title = str((c.title or extra.get("chapterTitle") or extra.get("original_filename") or "Video")).strip()
        video_group_items.setdefault(key, []).append((i, start, end, url, title))

    # For each video group, allocate letters by distinct (start,end) ranges in order of appearance.
    video_citation_to_letter: dict[int, str] = {}
    video_group_ranges: dict[str, list[tuple[str, float, float, str | None]]] = {}
    # key -> list of (letter, start, end, url)
    for key, items in video_group_items.items():
        seen_ranges: dict[tuple[int, int], str] = {}
        ranges_out: list[tuple[str, float, float, str | None]] = []
        letter_idx = 0
        for (orig_i, start, end, url, _title) in items:
            # Normalize to integer seconds for grouping.
            s_int = int(start) if start >= 0 else 0
            e_int = int(end) if end >= 0 else s_int
            rng_key = (s_int, e_int)
            letter = seen_ranges.get(rng_key)
            if not letter:
                base = chr(ord("a") + min(letter_idx, 25))
                letter = base
                seen_ranges[rng_key] = letter
                letter_idx += 1
                ranges_out.append((letter, float(s_int), float(e_int), url))
            video_citation_to_letter[orig_i] = letter
        video_group_ranges[key] = ranges_out

    def _sup_letter(letter: str) -> str:
        if not letter:
            return ""
        idx = ord(letter.lower()) - ord("a")
        if 0 <= idx < len(_SUPERSCRIPT_LETTERS):
            return _SUPERSCRIPT_LETTERS[idx]
        return letter

    def repl(m: re.Match) -> str:
        try:
            i = int(m.group(1))
        except Exception:
            return m.group(0)
        if not (1 <= i <= n):
            return m.group(0)

        c = citations[i - 1]
        extra = c.extra or {}
        href = f"#cm-src-{i}"
        if extra.get("type") == "video" and c.content_id:
            key = str(c.content_id)
            video_no = video_group_first_index.get(key, i)
            letter = video_citation_to_letter.get(i, "")
            display = f"{_sup_number(video_no)}{_sup_letter(letter)}"
            return f"[{display}]({href})"
        # Non-video citations use their direct citation index.
        return f"[{_sup_number(i)}]({href})"

    return _CITATION_RE.sub(repl, text)


async def _ensure_owned_course(db: AsyncSession, *, course_id: UUID, user_id: int) -> Course:
    res = await db.execute(select(Course).where(Course.id == course_id, Course.user_id == user_id))
    course = res.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


def _fmt_timestamp(seconds: float) -> str:
    try:
        s = max(0.0, float(seconds or 0.0))
    except Exception:
        s = 0.0
    mm = int(s // 60)
    ss = int(s % 60)
    return f"{mm}:{ss:02d}"


async def _build_video_context(
    *,
    db: AsyncSession,
    course_id: UUID,
    content_id: UUID | None,
    video_asset_id: UUID | None,
    max_chars: int = 6000,
) -> tuple[str, UUID | None]:
    """
    Build a compact, video-scoped context string for ephemeral VideoPlayer chat/summary.
    Returns (context, resolved_video_asset_id).
    """
    lines: list[str] = []
    resolved_asset_id: UUID | None = None

    if content_id is not None:
        cres = await db.execute(
            select(CourseContent).where(CourseContent.id == content_id, CourseContent.course_id == course_id)
        )
        content = cres.scalar_one_or_none()
        if content is not None:
            lines.append(f'Video title: {str(content.title or "").strip()}')
            desc = (content.description or "").strip()
            if desc:
                lines.append(f"Video description: {desc}")

    asset: VideoAsset | None = None
    if video_asset_id is not None:
        ares = await db.execute(
            select(VideoAsset).where(VideoAsset.id == video_asset_id, VideoAsset.course_id == course_id)
        )
        asset = ares.scalar_one_or_none()
    elif content_id is not None:
        ares = await db.execute(
            select(VideoAsset).where(VideoAsset.content_id == content_id, VideoAsset.course_id == course_id)
        )
        asset = ares.scalar_one_or_none()

    if asset is not None:
        resolved_asset_id = asset.id

        seg_res = await db.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.video_asset_id == asset.id)
            .order_by(TranscriptSegment.start_sec.asc())
        )
        segs = list(seg_res.scalars().all())
        if segs:
            lines.append("")
            lines.append("Transcript excerpt (timestamped):")
            budget = max(0, int(max_chars))
            used = sum(len(x) + 1 for x in lines)
            for seg in segs:
                row = f"[{_fmt_timestamp(seg.start_sec)}] {(seg.text or '').strip()}"
                if not row.strip():
                    continue
                if used + len(row) + 1 > budget:
                    lines.append("…")
                    break
                lines.append(row)
                used += len(row) + 1

    return ("\n".join([x for x in lines if x is not None]).strip(), resolved_asset_id)


@router.post("/courses/{course_id}/video-chat", response_model=VideoChatResponse)
async def course_video_chat(
    course_id: UUID,
    body: VideoChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> VideoChatResponse:
    """
    Ephemeral, video-scoped chat endpoint for the VideoPlayer page.

    - Does NOT persist conversations or messages.
    - Accepts client-provided history and optionally enriches with transcript excerpt.
    """
    course = await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    if body.content_id is None and body.video_asset_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="contentId or videoAssetId is required")

    # Cap history at the server boundary (trust-but-verify).
    max_n = int(settings.chat_history_max_messages)
    hist_items = (body.history or [])[-max_n:] if max_n > 0 else []
    history: list[ChatHistoryItem] = [
        ChatHistoryItem(role=h.role, content=(h.content or "").strip())
        for h in hist_items
        if (h.content or "").strip()
    ]

    video_ctx, _resolved_asset_id = await _build_video_context(
        db=db,
        course_id=course.id,
        content_id=body.content_id,
        video_asset_id=body.video_asset_id,
        max_chars=6000,
    )

    mode = str(body.mode or "chat").strip().lower()
    if mode not in {"chat", "summary"}:
        mode = "chat"

    if mode == "summary":
        user_message = "\n".join(
            [
                "Generate a comprehensive AI summary for the video lecture below.",
                "Requirements:",
                "- Cover main topics, key concepts, and takeaways.",
                "- Use clear sections and bullets where helpful.",
                "- If the transcript excerpt is missing or insufficient, say what you are missing.",
                "",
                video_ctx,
                "",
                "Now write the summary.",
            ]
        ).strip()
    else:
        user_q = (body.message or "").strip()
        if not user_q:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message is required for chat")
        user_message = "\n".join(
            [
                "The student is watching a video lecture. Use the video context below to answer the student's question.",
                "If the transcript excerpt is missing or insufficient, ask a clarifying question.",
                "",
                video_ctx,
                "",
                f"Student question: {user_q}",
            ]
        ).strip()

    engine = ChatEngine(settings)
    try:
        text, _citations = await engine.generate_reply(
            user_id=current_user.id,
            course_id=course.id,
            course_name=str(course.name),
            course_description=str(course.description or ""),
            history=history,
            user_message=user_message,
            rag_hits=None,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="LLM request failed") from e

    return VideoChatResponse(text=str(text or "").strip())


@router.post("/courses/{course_id}/chat", response_model=CourseChatResponse)
async def course_chat(
    course_id: UUID,
    body: CourseChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> CourseChatResponse:
    course = await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    conversation: ChatConversation | None = None
    created_new_conversation = False
    if body.conversation_id:
        try:
            convo_id = UUID(str(body.conversation_id))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid conversationId")

        res = await db.execute(
            select(ChatConversation)
            .where(ChatConversation.id == convo_id, ChatConversation.course_id == course.id)
        )
        conversation = res.scalar_one_or_none()
        if conversation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if conversation is None:
        conversation = ChatConversation(course_id=course.id, title=None)
        db.add(conversation)
        await db.flush()  # get conversation.id
        created_new_conversation = True

    # Load last N messages (asc) for conversational context.
    # Do this BEFORE inserting the new user message to avoid duplicate user_message in history.
    max_n = int(settings.chat_history_max_messages)
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(max_n)
    )
    res = await db.execute(stmt)
    recent_desc = list(res.scalars().all())
    recent_asc = list(reversed(recent_desc))
    history = [ChatHistoryItem(role=m.role, content=m.content) for m in recent_asc]

    # Persist user message.
    db.add(ChatMessage(conversation_id=conversation.id, role="user", content=body.message))

    # Optional DSPy router: decide whether to retrieve course content or answer generally.
    should_retrieve = bool(settings.rag_enabled)
    if settings.dspy_router_enabled and str(getattr(settings, "environment", "")).strip().lower() != "test":
        history_summary = "\n".join([f"{h.role}: {h.content}" for h in history[-6:]]).strip()
        decision = route_message(
            settings=settings,
            course_name=str(course.name),
            course_description=str(course.description or ""),
            history=history_summary,
            message=str(body.message),
        )
        # Retrieve for course-specific and mixed queries.
        should_retrieve = bool(settings.rag_enabled) and bool(decision.needs_course_retrieval)

    rag_hits = []
    if should_retrieve:
        try:
            # IMPORTANT: Postgres errors can abort the current transaction. Retrieval is best-effort
            # (we can fall back to non-RAG chat), so run retrieval in an isolated read-only session
            # to avoid poisoning the write transaction used for message persistence.
            SessionLocal = get_session_maker()
            async with SessionLocal() as rag_db:
                rag_hits = await retrieve_course_hybrid_hits(
                    db=rag_db,
                    course_id=course.id,
                    query=body.message,
                    cfg=HybridRetrieveConfig(
                        lexical_k=max(10, int(settings.rag_top_k) * 3),
                        semantic_k=max(10, int(settings.rag_top_k) * 3),
                        top_k=int(settings.rag_top_k),
                        rrf_k0=60,
                    ),
                    categories=None,
                )
        except Exception as e:
            logger.warning("RAG retrieval failed (best-effort): %s", str(e))
            rag_hits = []

    # LLM reply (with optional RAG context).
    try:
        engine = ChatEngine(settings)
        # Best-effort: if this is a brand new conversation, generate a short title from the first message.
        # If it fails for any reason, proceed without a title (UI already falls back to "Conversation").
        if created_new_conversation and not conversation.title:
            try:
                title = await engine.generate_title(
                    course_name=course.name,
                    first_user_message=body.message,
                )
                if title:
                    conversation.title = title
            except Exception:
                pass

        reply, citations = await engine.generate_reply(
            user_id=current_user.id,
            course_id=course.id,
            course_name=course.name,
            course_description=course.description,
            history=history,
            user_message=body.message,
            rag_hits=rag_hits,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e)) from e
    except Exception as e:
        # Persist a visible assistant error message so the conversation doesn't look "stuck".
        # This also ensures the user message is not lost from chat history.
        logger.warning("LLM request failed (persisting error reply): %s", str(e))
        reply = (
            "I couldn’t reach the language model right now. "
            "Please retry in a moment—if this keeps happening, check your API key/quota and server logs."
        )
        citations = []

    # Best-effort: attach URLs to citations, then inject inline citation links into the reply.
    citations = await _attach_citation_urls(db=db, settings=settings, course_id=course.id, citations=citations)
    reply_with_links = _format_reply_with_citation_links(reply, citations)

    # Persist citations separately (JSONB) so the frontend can render a structured Sources section.
    db.add(
        ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=reply_with_links,
            # Use JSON mode so UUIDs become strings (JSONB must be JSON-serializable).
            citations=[c.model_dump(mode="json") for c in citations] if citations else None,
        )
    )

    # Bump conversation activity.
    conversation.last_message_at = datetime.now(timezone.utc)

    await db.commit()

    return CourseChatResponse(text=reply_with_links, citations=citations, conversation_id=conversation.id)


@router.get("/courses/{course_id}/conversations", response_model=list[ChatConversationPublic])
async def list_conversations(
    course_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatConversation]:
    await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)
    res = await db.execute(
        select(ChatConversation)
        .where(ChatConversation.course_id == course_id)
        .order_by(ChatConversation.last_message_at.desc())
    )
    return list(res.scalars().all())


@router.get("/conversations/{conversation_id}/messages", response_model=list[ChatMessagePublic])
async def list_messages(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatMessage]:
    # Ownership: join conversations -> courses and ensure the current user owns the course.
    res = await db.execute(
        select(ChatConversation, Course)
        .join(Course, Course.id == ChatConversation.course_id)
        .where(ChatConversation.id == conversation_id, Course.user_id == current_user.id)
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    msgs = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
    )
    return list(msgs.scalars().all())


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    # Ownership: join conversations -> courses and ensure the current user owns the course.
    res = await db.execute(
        select(ChatConversation)
        .join(Course, Course.id == ChatConversation.course_id)
        .where(ChatConversation.id == conversation_id, Course.user_id == current_user.id)
    )
    conversation = res.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    await db.delete(conversation)
    await db.commit()
    return {"ok": True}
