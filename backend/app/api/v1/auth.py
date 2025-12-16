from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    clear_access_cookie,
    clear_refresh_cookie,
    create_csrf_token,
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    set_csrf_cookie,
    set_access_cookie,
    set_refresh_cookie,
    verify_password,
)
from app.core.settings import Settings, get_settings
from app.db.models.refresh_session import RefreshSession
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import (
    CsrfResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RefreshResponse,
    SignupRequest,
    SignupResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/csrf", response_model=CsrfResponse)
async def csrf(settings: Settings = Depends(get_settings)):
    token = create_csrf_token()
    response = JSONResponse(CsrfResponse(ok=True, csrfToken=token).model_dump())
    response.headers["Cache-Control"] = "no-store"
    set_csrf_cookie(response=response, token=token, settings=settings)
    return response


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

    access_token = create_access_token(
        subject=str(user.id),
        ttl_seconds=settings.jwt_access_ttl_seconds,
        secret=settings.jwt_secret,
    )

    response = JSONResponse(LoginResponse(ok=True).model_dump())

    # Refresh session (opaque token stored as cookie, hashed in DB).
    now = datetime.now(timezone.utc)
    refresh_token = create_refresh_token()
    refresh_token_hash = hash_refresh_token(refresh_token, settings.jwt_secret)
    refresh_expires_at = now + timedelta(seconds=int(settings.jwt_refresh_ttl_seconds))

    db.add(
        RefreshSession(
            user_id=user.id,
            token_hash=refresh_token_hash,
            expires_at=refresh_expires_at,
        )
    )
    await db.commit()

    set_access_cookie(response=response, token=access_token, settings=settings)
    set_refresh_cookie(response=response, token=refresh_token, settings=settings)
    return response


@router.post("/signup", response_model=SignupResponse)
async def signup(
    body: SignupRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    email = body.email.lower().strip()
    display_name = (body.display_name or "").strip()

    if not display_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required")
    if len(display_name) > 120:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name is too long")
    if len(body.password or "") < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters",
        )

    # Fast path: pre-check email uniqueness for a clean 409; still handle race via IntegrityError.
    res = await db.execute(select(User).where(User.email == email))
    if res.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=email, hashed_password=hash_password(body.password), display_name=display_name)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    await db.refresh(user)

    access_token = create_access_token(
        subject=str(user.id),
        ttl_seconds=settings.jwt_access_ttl_seconds,
        secret=settings.jwt_secret,
    )

    # Refresh session (opaque token stored as cookie, hashed in DB).
    now = datetime.now(timezone.utc)
    refresh_token = create_refresh_token()
    refresh_token_hash = hash_refresh_token(refresh_token, settings.jwt_secret)
    refresh_expires_at = now + timedelta(seconds=int(settings.jwt_refresh_ttl_seconds))

    db.add(
        RefreshSession(
            user_id=user.id,
            token_hash=refresh_token_hash,
            expires_at=refresh_expires_at,
        )
    )
    await db.commit()

    response = JSONResponse(SignupResponse(ok=True).model_dump())
    set_access_cookie(response=response, token=access_token, settings=settings)
    set_refresh_cookie(response=response, token=refresh_token, settings=settings)
    return response


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    refresh_token_hash = hash_refresh_token(refresh_token, settings.jwt_secret)
    now = datetime.now(timezone.utc)

    res = await db.execute(
        select(RefreshSession).where(
            RefreshSession.token_hash == refresh_token_hash,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.expires_at > now,
        )
    )
    session = res.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user_res = await db.execute(select(User).where(User.id == session.user_id))
    user = user_res.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    # Rotate refresh token.
    new_refresh_token = create_refresh_token()
    new_refresh_hash = hash_refresh_token(new_refresh_token, settings.jwt_secret)
    new_refresh_expires_at = now + timedelta(seconds=int(settings.jwt_refresh_ttl_seconds))

    new_session = RefreshSession(
        id=uuid4(),
        user_id=user.id,
        token_hash=new_refresh_hash,
        expires_at=new_refresh_expires_at,
    )

    # Ensure the new session exists before pointing the old one at it.
    db.add(new_session)
    await db.flush()

    session.revoked_at = now
    session.replaced_by_id = new_session.id
    await db.commit()

    access_token = create_access_token(
        subject=str(user.id),
        ttl_seconds=settings.jwt_access_ttl_seconds,
        secret=settings.jwt_secret,
    )

    response = JSONResponse(RefreshResponse(ok=True).model_dump())
    set_access_cookie(response=response, token=access_token, settings=settings)
    set_refresh_cookie(response=response, token=new_refresh_token, settings=settings)
    return response


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if refresh_token:
        refresh_token_hash = hash_refresh_token(refresh_token, settings.jwt_secret)
        now = datetime.now(timezone.utc)
        res = await db.execute(select(RefreshSession).where(RefreshSession.token_hash == refresh_token_hash))
        session = res.scalar_one_or_none()
        if session is not None and session.revoked_at is None:
            session.revoked_at = now
            await db.commit()

    response = JSONResponse(LogoutResponse(ok=True).model_dump())
    clear_access_cookie(response=response, settings=settings)
    clear_refresh_cookie(response=response, settings=settings)
    return response
