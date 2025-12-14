from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, set_access_cookie, verify_password
from app.core.settings import Settings, get_settings
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    res = await db.execute(select(User).where(User.email == body.email))
    user = res.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(
        subject=str(user.id),
        ttl_seconds=settings.jwt_access_ttl_seconds,
        secret=settings.jwt_secret,
    )

    response = JSONResponse(LoginResponse(ok=True).model_dump())
    set_access_cookie(response=response, token=token, settings=settings)
    return response
