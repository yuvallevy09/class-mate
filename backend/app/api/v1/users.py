from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import clear_access_cookie, clear_refresh_cookie
from app.core.settings import Settings, get_settings
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.user import UserPublic

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.delete("/me")
async def delete_me(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    await db.delete(current_user)
    await db.commit()

    response = JSONResponse({"ok": True}, status_code=status.HTTP_200_OK)
    clear_access_cookie(response=response, settings=settings)
    clear_refresh_cookie(response=response, settings=settings)
    return response
