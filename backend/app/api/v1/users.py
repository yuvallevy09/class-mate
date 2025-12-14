from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.db.models.user import User
from app.schemas.user import UserPublic

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
