from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models.course import Course
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.chat import CourseChatRequest, CourseChatResponse

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

    # v0 stub: echo back a deterministic response; keep API contract stable.
    # Later: build prompt from course + contents, persist conversation/messages, add citations.
    reply = f"(stub) {course.name}: {body.message}"
    return CourseChatResponse(text=reply, citations=[])
