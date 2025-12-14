from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from typing import Any

import bcrypt
import jwt
from jwt import InvalidTokenError


def hash_password(plain: str) -> str:
    hashed = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(*, subject: str, ttl_seconds: int, secret: str) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=ttl_seconds)
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": exp,
        "iat": now,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
    except InvalidTokenError as e:
        raise ValueError("Invalid token") from e


def create_refresh_token() -> str:
    # Opaque, random, URL-safe token suitable for cookies.
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str, secret: str) -> str:
    # Store only a keyed hash so DB leaks can't be replayed.
    return hmac.new(
        key=secret.encode("utf-8"),
        msg=token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def set_access_cookie(*, response, token: str, settings) -> None:
    response.set_cookie(
        key=settings.access_cookie_name,
        value=token,
        httponly=True,
        secure=bool(settings.cookie_secure),
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        max_age=int(settings.jwt_access_ttl_seconds),
        path="/",
    )


def clear_access_cookie(*, response, settings) -> None:
    response.delete_cookie(
        key=settings.access_cookie_name,
        domain=settings.cookie_domain,
        path="/",
    )


def set_refresh_cookie(*, response, token: str, settings) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        httponly=True,
        secure=bool(settings.cookie_secure),
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        max_age=int(settings.jwt_refresh_ttl_seconds),
        path=settings.refresh_cookie_path,
    )


def clear_refresh_cookie(*, response, settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        domain=settings.cookie_domain,
        path=settings.refresh_cookie_path,
    )
