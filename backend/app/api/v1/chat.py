from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from uuid import UUID

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, HTTPException, status
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
from app.db.models.user import User
from app.db.session import get_db, get_session_maker
from app.rag.hybrid_retrieve import HybridRetrieveConfig, retrieve_course_hybrid_hits
from app.schemas.chat import ChatCitation, CourseChatRequest, CourseChatResponse
from app.schemas.chat_persistence import ChatConversationPublic, ChatMessagePublic

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


def _s3_client(settings: Settings):
    kwargs: dict = {"service_name": "s3", "region_name": settings.s3_region}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
        kwargs["config"] = Config(s3={"addressing_style": "path"})
    if settings.s3_access_key_id and settings.s3_secret_access_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    return boto3.client(**kwargs)


async def _attach_citation_urls(
    *,
    db: AsyncSession,
    settings: Settings,
    course_id: UUID,
    citations: list[ChatCitation],
) -> list[ChatCitation]:
    """Best-effort: attach presigned download URLs for citations (when possible)."""
    if not citations or not settings.s3_bucket:
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
    file_key_by_id: dict[UUID, str] = {cid: str(key) for (cid, key) in res.all() if key}
    if not file_key_by_id:
        return citations

    s3 = _s3_client(settings)
    for c in citations:
        if c.content_id and not c.url:
            fk = file_key_by_id.get(c.content_id)
            if not fk:
                continue
            try:
                c.url = s3.generate_presigned_url(
                    ClientMethod="get_object",
                    Params={"Bucket": settings.s3_bucket, "Key": fk},
                    ExpiresIn=int(settings.s3_download_expires_seconds),
                )
            except Exception:
                pass
    return citations


_CITATION_RE = re.compile(r"\[(?:#\s*)?(\d{1,3})\]")


def _format_reply_with_sources(reply: str, citations: list[ChatCitation]) -> str:
    """
    Convert inline citation markers like [1] or [#1] into markdown footnote references [^1],
    and append footnote definitions containing a one-line source description + link.
    """
    text = (reply or "").strip()
    if not citations:
        return text

    n = len(citations)

    def repl(m: re.Match) -> str:
        try:
            i = int(m.group(1))
        except Exception:
            return m.group(0)
        return f"[^{i}]" if 1 <= i <= n else m.group(0)

    text = _CITATION_RE.sub(repl, text)

    # NOTE: We do NOT add a "Sources" header here because remark-gfm renders footnotes
    # into a dedicated section with its own heading. We customize that heading in the UI.
    lines: list[str] = [""]
    for i, c in enumerate(citations, start=1):
        extra = c.extra or {}
        original = str(extra.get("original_filename") or "").strip()
        title = (c.title or "").strip()
        label = (title or original or (str(c.content_id) if c.content_id else "Course content")).strip()

        if extra.get("type") == "video":
            # Let the frontend detect "Open source" as a video link and open an in-app player.
            # Put the display title in the link title attribute (markdown: (url "title")).
            video_title = (title or original or "Video").strip().replace('"', "").replace("\n", " ")
            try:
                s = float(extra.get("startSec") or 0.0)
                e = float(extra.get("endSec") or 0.0)
                label = f"{label} (video {s:.0f}s–{e:.0f}s)"
            except Exception:
                label = f"{label} (video)"
        elif extra.get("pageStart") or extra.get("pageEnd"):
            try:
                ps = int(extra.get("pageStart") or extra.get("pageEnd") or 0)
                pe = int(extra.get("pageEnd") or extra.get("pageStart") or ps)
                if ps and pe:
                    label = f"{label} (p.{ps}" + (f"–{pe}" if pe != ps else "") + ")"
            except Exception:
                pass

        if c.url:
            # Single link only (UI hides the autogenerated backrefs).
            if extra.get("type") == "video":
                lines.append(f'[^{i}]: {label} — [Open source]({c.url} "video:{video_title}")')
            else:
                lines.append(f"[^{i}]: {label} — [Open source]({c.url})")
        else:
            lines.append(f"[^{i}]: {label}")

    return (text + "\n" + "\n".join(lines)).strip() + "\n"


async def _ensure_owned_course(db: AsyncSession, *, course_id: UUID, user_id: int) -> Course:
    res = await db.execute(select(Course).where(Course.id == course_id, Course.user_id == user_id))
    course = res.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


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
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="LLM request failed") from e

    # Best-effort: attach presigned URLs to citations, then append a Sources section to the reply
    # so citations are readable and persistent in chat history.
    citations = await _attach_citation_urls(db=db, settings=settings, course_id=course.id, citations=citations)
    reply_with_sources = _format_reply_with_sources(reply, citations)

    db.add(ChatMessage(conversation_id=conversation.id, role="assistant", content=reply_with_sources))

    # Bump conversation activity.
    conversation.last_message_at = datetime.now(timezone.utc)

    await db.commit()

    return CourseChatResponse(text=reply_with_sources, citations=citations, conversation_id=conversation.id)


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
