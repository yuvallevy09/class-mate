"""Shared citation/formatting helpers for the chat endpoints.

These were originally private helpers in `app/api/v1/chat.py` (the legacy v1
endpoint). They are pure, reusable building blocks — enrich citations with URLs
and chapter titles, format inline citation markers, and the course-ownership
guard — so they live here as a neutral service that both the conversation CRUD
endpoints and the live v2 chat endpoint depend on.
"""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings
from app.db.models.course import Course
from app.db.models.course_content import CourseContent
from app.db.models.video_asset import VideoAsset
from app.db.models.video_chapter import VideoChapter
from app.schemas.chat import ChatCitation


async def ensure_owned_course(db: AsyncSession, *, course_id: UUID, user_id: int) -> Course:
    res = await db.execute(select(Course).where(Course.id == course_id, Course.user_id == user_id))
    course = res.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


async def attach_citation_urls(
    *,
    db: AsyncSession,
    settings: Settings,
    course_id: UUID,
    citations: list[ChatCitation],
) -> list[ChatCitation]:
    """Best-effort: attach URLs for citations (when possible).

    - For video transcript citations, prefer a stable in-app VideoPlayer URL (no S3 required).
    - For file-backed citations, prefer a stable in-app API link that redirects to a fresh presigned URL.
    """
    if not citations:
        return citations

    # Video citations: link to the in-app VideoPlayer page at the relevant timestamp.
    # This is stable across sessions and avoids leaking raw presigned URLs into chat history.
    for c in citations:
        extra = c.extra or {}
        if extra.get("type") == "video" and c.content_id and not c.url:
            try:
                start = float(extra.get("startSec") or 0.0)
            except Exception:
                start = 0.0
            # Use an integer second for URL cleanliness.
            t = int(start) if start >= 0 else 0
            c.url = f"/VideoPlayer?courseId={course_id}&contentId={c.content_id}&t={t}"

    if not settings.s3_bucket:
        return citations

    content_ids: list[UUID] = []
    for c in citations:
        if c.content_id and not c.url:
            content_ids.append(c.content_id)
    if not content_ids:
        return citations

    res = await db.execute(
        select(CourseContent.id, CourseContent.file_key).where(
            CourseContent.course_id == course_id,
            CourseContent.id.in_(content_ids),
            CourseContent.file_key.is_not(None),
        )
    )
    has_file_by_id: set[UUID] = {cid for (cid, key) in res.all() if cid and key}
    if not has_file_by_id:
        return citations

    for c in citations:
        if c.content_id and not c.url:
            if c.content_id not in has_file_by_id:
                continue
            # Stable link: API endpoint will redirect to a fresh presigned URL on click.
            c.url = f"/api/v1/contents/{c.content_id}/download-redirect"
    return citations


async def attach_video_chapter_titles(
    *,
    db: AsyncSession,
    course_id: UUID,
    citations: list[ChatCitation],
) -> list[ChatCitation]:
    """
    Best-effort: attach `chapterTitle` for video citations based on `chapterId`.

    We prefer resolving via the `video_chapters` table (first-class artifact) rather than
    relying on chunk metadata, so citations remain stable even if chunk meta changes.
    """
    if not citations:
        return citations

    chapter_ids: set[UUID] = set()
    for c in citations:
        extra = c.extra or {}
        if str(extra.get("type") or "").lower() != "video":
            continue
        if extra.get("chapterTitle"):
            continue
        raw = extra.get("chapterId") or extra.get("chapter_id")
        if not raw:
            continue
        try:
            chapter_ids.add(UUID(str(raw)))
        except ValueError:
            continue

    if not chapter_ids:
        return citations

    res = await db.execute(
        select(VideoChapter.id, VideoChapter.title)
        .join(VideoAsset, VideoAsset.id == VideoChapter.video_asset_id)
        .where(VideoAsset.course_id == course_id, VideoChapter.id.in_(list(chapter_ids)))
    )
    title_by_id: dict[str, str] = {str(cid): str(title or "").strip() for (cid, title) in res.all()}
    if not title_by_id:
        return citations

    for c in citations:
        extra = c.extra or {}
        if str(extra.get("type") or "").lower() != "video":
            continue
        if extra.get("chapterTitle"):
            continue
        raw = extra.get("chapterId") or extra.get("chapter_id")
        if not raw:
            continue
        title = title_by_id.get(str(raw))
        if title:
            extra["chapterTitle"] = title
            c.extra = extra
    return citations


# Optional ( ) before and (\.) after: period goes before the link; leading space is dropped so "...cloud. ¹ᵃ" not "...cloud . ¹ᵃ".
_CITATION_RE = re.compile(r"( )?\[(?:#\s*)?(\d{1,3})\](\.)?")


_SUPERSCRIPT_DIGITS = {
    "0": "⁰",
    "1": "¹",
    "2": "²",
    "3": "³",
    "4": "⁴",
    "5": "⁵",
    "6": "⁶",
    "7": "⁷",
    "8": "⁸",
    "9": "⁹",
}


def _sup_number(n: int) -> str:
    s = str(max(0, int(n)))
    return "".join(_SUPERSCRIPT_DIGITS.get(ch, ch) for ch in s)


def format_reply_with_citation_links(reply: str, citations: list[ChatCitation]) -> str:
    """
    Convert inline citation markers like [1] or [#1] into in-page links that the frontend
    can use to scroll to a structured "Sources" section rendered from `citations`.

    We intentionally do NOT append sources/footnotes into the message content. This keeps
    chat text clean and avoids persisting brittle markdown footnote internals.
    """
    text = (reply or "").strip()
    if not citations:
        return text

    n = len(citations)

    # Video citations UX:
    # - The footnote number represents the video (we use the first citation index for that video).
    # - A letter represents the time range (a, b, c...) and is shown as a superscript character
    #   adjacent to the footnote marker in the answer.
    # - The footnote definition for the video contains a grouped list of ranges with links that
    #   open the in-app VideoPlayer (frontend opens in a new tab based on title="video:...").
    #
    # This keeps the main answer clean and makes sources more navigable.
    _SUPERSCRIPT_LETTERS = [
        "ᵃ",  # a
        "ᵇ",  # b
        "ᶜ",  # c
        "ᵈ",  # d
        "ᵉ",  # e
        "ᶠ",  # f
        "ᵍ",  # g
        "ʰ",  # h
        "ⁱ",  # i
        "ʲ",  # j
        "ᵏ",  # k
        "ˡ",  # l
        "ᵐ",  # m
        "ⁿ",  # n
        "ᵒ",  # o
        "ᵖ",  # p
        "ʳ",  # r
        "ˢ",  # s
        "ᵗ",  # t
        "ᵘ",  # u
        "ᵛ",  # v
        "ʷ",  # w
        "ˣ",  # x
        "ʸ",  # y
        "ᶻ",  # z
    ]

    # Group video citations by content_id; "number" is the first citation index where it appears.
    video_group_first_index: dict[str, int] = {}
    video_group_items: dict[str, list[tuple[int, float, float, str | None, str | None]]] = {}
    # key -> list of (original_citation_index, start_sec, end_sec, url, title_for_link)

    for i, c in enumerate(citations, start=1):
        extra = c.extra or {}
        if extra.get("type") != "video" or not c.content_id:
            continue
        key = str(c.content_id)
        video_group_first_index[key] = min(video_group_first_index.get(key, i), i)
        try:
            start = float(extra.get("startSec") or 0.0)
        except Exception:
            start = 0.0
        try:
            end = float(extra.get("endSec") or 0.0)
        except Exception:
            end = start
        url = str(c.url) if c.url else None
        title = str((c.title or extra.get("chapterTitle") or extra.get("original_filename") or "Video")).strip()
        video_group_items.setdefault(key, []).append((i, start, end, url, title))

    # For each video group, allocate letters by distinct (start,end) ranges in order of appearance.
    video_citation_to_letter: dict[int, str] = {}
    video_group_ranges: dict[str, list[tuple[str, float, float, str | None]]] = {}
    # key -> list of (letter, start, end, url)
    for key, items in video_group_items.items():
        seen_ranges: dict[tuple[int, int], str] = {}
        ranges_out: list[tuple[str, float, float, str | None]] = []
        letter_idx = 0
        for (orig_i, start, end, url, _title) in items:
            # Normalize to integer seconds for grouping.
            s_int = int(start) if start >= 0 else 0
            e_int = int(end) if end >= 0 else s_int
            rng_key = (s_int, e_int)
            letter = seen_ranges.get(rng_key)
            if not letter:
                base = chr(ord("a") + min(letter_idx, 25))
                letter = base
                seen_ranges[rng_key] = letter
                letter_idx += 1
                ranges_out.append((letter, float(s_int), float(e_int), url))
            video_citation_to_letter[orig_i] = letter
        video_group_ranges[key] = ranges_out

    def _sup_letter(letter: str) -> str:
        if not letter:
            return ""
        idx = ord(letter.lower()) - ord("a")
        if 0 <= idx < len(_SUPERSCRIPT_LETTERS):
            return _SUPERSCRIPT_LETTERS[idx]
        return letter

    def repl(m: re.Match) -> str:
        try:
            i = int(m.group(2))
        except Exception:
            return m.group(0)
        if not (1 <= i <= n):
            return m.group(0)

        c = citations[i - 1]
        extra = c.extra or {}
        href = f"#cm-src-{i}"
        if extra.get("type") == "video" and c.content_id:
            key = str(c.content_id)
            video_no = video_group_first_index.get(key, i)
            letter = video_citation_to_letter.get(i, "")
            display = f"{_sup_number(video_no)}{_sup_letter(letter)}"
            link = f"[{display}]({href})"
        else:
            link = f"[{_sup_number(i)}]({href})"
        # If the citation was immediately before a period, put the period first then the link (no space before the period).
        if m.group(3) == ".":
            return ". " + link
        # Preserve any space that was before the citation when there's no trailing period.
        return (m.group(1) or "") + link

    return _CITATION_RE.sub(repl, text)
