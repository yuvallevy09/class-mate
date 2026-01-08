from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chat_engine import ChatEngine, ChatHistoryItem
from app.api.deps import get_current_user
from app.core.settings import Settings, get_settings
from app.db.models.chat_conversation import ChatConversation
from app.db.models.chat_message import ChatMessage
from app.db.models.course import Course
from app.db.models.user import User
from app.db.session import get_db
from app.rag.pg_retrieve import retrieve_course_chunk_hits
from app.schemas.chat import CourseChatRequest, CourseChatResponse
from app.schemas.chat_persistence import ChatConversationPublic, ChatMessagePublic

router = APIRouter(tags=["chat"])


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

    # Retrieval: Postgres single source of truth (FTS).
    # Router-selected categories will be added later; for now search across all categories.
    rag_hits = []
    if settings.rag_enabled:
        try:
            rag_hits = await retrieve_course_chunk_hits(
                db=db,
                course_id=course.id,
                query=body.message,
                top_k=int(settings.rag_top_k),
                categories=None,
            )
        except Exception:
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

    db.add(ChatMessage(conversation_id=conversation.id, role="assistant", content=reply))

    # Bump conversation activity.
    conversation.last_message_at = datetime.now(timezone.utc)

    await db.commit()

    return CourseChatResponse(text=reply, citations=citations, conversation_id=conversation.id)


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
