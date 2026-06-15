from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.course_info import Lecture


class ViewingContext(BaseModel):
    """Resolved state describing the lecture a student is currently watching.

    Built once per chat turn in the endpoint from the raw
    `CourseChatRequest.watching_*` fields, so the agent never touches request
    fields or re-resolves UUIDs. Carrying the *resolved* lecture (slug, id,
    title) means downstream code can scope retrieval (soft-scope the watched
    lecture into routing) and anchor the answer ("currently watching L3 at
    12:45") without another `CourseInfo` lookup.

    Construct via `from_lecture`, which enforces the readiness rule: there is no
    useful viewing context for a lecture whose transcript hasn't been ingested,
    so resolution returns None and the caller falls back to ordinary course chat.
    """

    model_config = ConfigDict(frozen=True)

    lecture_id: UUID
    lecture_slug: str
    lecture_title: str
    # Current playback head, in seconds. None when the player sent a watched
    # lecture but no position (e.g. not yet started); the recent-window anchor
    # is skipped in that case, but soft-scoping still applies.
    timestamp_sec: float | None = None

    @classmethod
    def from_lecture(
        cls, lecture: Lecture, *, timestamp_sec: float | None
    ) -> "ViewingContext | None":
        """Resolve a viewing context, or None when it can't be useful.

        Returns None when the lecture's transcript isn't ingested yet — without
        a transcript there's nothing to scope to or anchor against, so the
        caller should treat the turn as ordinary course chat.
        """
        if not lecture.transcript_ready:
            return None
        ts = float(timestamp_sec) if timestamp_sec is not None and timestamp_sec >= 0 else None
        return cls(
            lecture_id=lecture.id,
            lecture_slug=lecture.slug,
            lecture_title=lecture.title,
            timestamp_sec=ts,
        )
