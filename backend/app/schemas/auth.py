from __future__ import annotations

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    ok: bool = True


class RefreshResponse(BaseModel):
    ok: bool = True


class LogoutResponse(BaseModel):
    ok: bool = True
