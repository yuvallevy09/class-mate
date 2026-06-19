from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models.chat_conversation import ChatConversation
from app.db.models.chat_message import ChatMessage
from app.db.models.course import Course
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.chat_persistence import ChatConversationPublic, ChatMessagePublic
from app.services.chat_citations import ensure_owned_course

router = APIRouter(tags=["conversations"])
logger = logging.getLogger(__name__)


@router.get("/courses/{course_id}/conversations", response_model=list[ChatConversationPublic])
async def list_conversations(
    course_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    video_asset_id: UUID | None = Query(
        default=None,
        description="When set, return only conversations anchored to this lecture "
        "(the video-chat widget's per-video history). When omitted, returns only "
        "course-level conversations (the course chat) — per-lecture threads are "
        "excluded and live on their video page.",
    ),
) -> list[ChatConversation]:
    await ensure_owned_course(db, course_id=course_id, user_id=current_user.id)
    stmt = select(ChatConversation).where(ChatConversation.course_id == course_id)
    if video_asset_id is not None:
        stmt = stmt.where(ChatConversation.video_asset_id == video_asset_id)
    else:
        # Course chat and video chat are distinct spaces: the bare list is the
        # course-level history only (video-tagged threads live on their lecture
        # page, fetched with ?video_asset_id=).
        stmt = stmt.where(ChatConversation.video_asset_id.is_(None))
    stmt = stmt.order_by(ChatConversation.last_message_at.desc())
    res = await db.execute(stmt)
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
