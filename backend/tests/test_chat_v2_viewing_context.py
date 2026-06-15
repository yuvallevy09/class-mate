"""Unit tests for `_resolve_viewing_context` — the request → ViewingContext
resolver in the chat-v2 endpoints. Pure (no DB/LLM): just CourseInfo lookups
and the graceful-degrade rules that disable video mode safely.
"""

from __future__ import annotations

from uuid import uuid4

from app.api.v1.chat_v2 import _resolve_viewing_context
from app.schemas.course_info import CourseInfo, Lecture


READY_ID = uuid4()
PENDING_ID = uuid4()


def _course_info() -> CourseInfo:
    return CourseInfo(
        id=uuid4(),
        name="ML",
        lectures=[
            Lecture(id=READY_ID, content_id=uuid4(), slug="L1", title="Intro", transcript_ready=True),
            Lecture(id=PENDING_ID, content_id=uuid4(), slug="L2", title="Pending", transcript_ready=False),
        ],
    )


def test_resolves_ready_lecture() -> None:
    vc = _resolve_viewing_context(
        course_info=_course_info(),
        watching_video_asset_id=READY_ID,
        watching_timestamp_sec=90.0,
    )
    assert vc is not None
    assert vc.lecture_slug == "L1"
    assert vc.lecture_title == "Intro"
    assert vc.timestamp_sec == 90.0


def test_none_when_no_asset_sent() -> None:
    assert (
        _resolve_viewing_context(
            course_info=_course_info(),
            watching_video_asset_id=None,
            watching_timestamp_sec=90.0,
        )
        is None
    )


def test_none_when_asset_not_in_course() -> None:
    assert (
        _resolve_viewing_context(
            course_info=_course_info(),
            watching_video_asset_id=uuid4(),  # unknown id
            watching_timestamp_sec=90.0,
        )
        is None
    )


def test_none_when_transcript_not_ready() -> None:
    assert (
        _resolve_viewing_context(
            course_info=_course_info(),
            watching_video_asset_id=PENDING_ID,
            watching_timestamp_sec=90.0,
        )
        is None
    )


def test_resolves_without_timestamp() -> None:
    vc = _resolve_viewing_context(
        course_info=_course_info(),
        watching_video_asset_id=READY_ID,
        watching_timestamp_sec=None,
    )
    assert vc is not None
    assert vc.timestamp_sec is None
