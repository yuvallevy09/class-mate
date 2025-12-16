from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    ok: bool = True


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str = Field(validation_alias="displayName")


class SignupResponse(BaseModel):
    ok: bool = True


class RefreshResponse(BaseModel):
    ok: bool = True


class LogoutResponse(BaseModel):
    ok: bool = True


class CsrfResponse(BaseModel):
    ok: bool = True
    csrfToken: str
