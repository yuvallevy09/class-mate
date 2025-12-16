from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models.chat_conversation import ChatConversation
from app.db.models.chat_message import ChatMessage
from app.db.models.course import Course
from app.db.models.user import User
from app.db.session import get_db
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
) -> CourseChatResponse:
    course = await _ensure_owned_course(db, course_id=course_id, user_id=current_user.id)

    conversation: ChatConversation | None = None
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

    # Persist user message.
    db.add(ChatMessage(conversation_id=conversation.id, role="user", content=body.message))

    # v0 stub: echo back a deterministic response; keep API contract stable.
    # Later: build prompt from course + contents, add citations, streaming, etc.
    reply = f"(stub) {course.name}: {body.message}"
    db.add(ChatMessage(conversation_id=conversation.id, role="assistant", content=reply))

    # Bump conversation activity.
    conversation.last_message_at = datetime.now(timezone.utc)

    await db.commit()

    return CourseChatResponse(text=reply, citations=[], conversation_id=conversation.id)


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
