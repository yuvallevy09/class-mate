from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, users, courses, course_contents, uploads

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(courses.router)
api_router.include_router(course_contents.router)
api_router.include_router(uploads.router)
