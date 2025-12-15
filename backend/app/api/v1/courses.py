from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models.course import Course
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.course import CourseCreate, CoursePublic

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("", response_model=list[CoursePublic])
async def list_courses(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Course]:
    res = await db.execute(
        select(Course)
        .where(Course.user_id == current_user.id)
        .order_by(Course.created_at.desc())
    )
    return list(res.scalars().all())


@router.post("", response_model=CoursePublic)
async def create_course(
    body: CourseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Course:
    course = Course(
        user_id=current_user.id,
        name=body.name.strip(),
        description=body.description,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return course


@router.get("/{course_id}", response_model=CoursePublic)
async def get_course(
    course_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Course:
    res = await db.execute(
        select(Course).where(
            Course.id == course_id,
            Course.user_id == current_user.id,
        )
    )
    course = res.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


@router.delete("/{course_id}")
async def delete_course(
    course_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    res = await db.execute(
        select(Course).where(
            Course.id == course_id,
            Course.user_id == current_user.id,
        )
    )
    course = res.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    await db.delete(course)
    await db.commit()
    return {"ok": True}


