from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def _fmt_timestamp(seconds: float | int | None) -> str:
    """Render seconds as M:SS for prompt strings."""
    try:
        s = max(0.0, float(seconds or 0.0))
    except (TypeError, ValueError):
        s = 0.0
    mm = int(s // 60)
    ss = int(s % 60)
    return f"{mm}:{ss:02d}"


class LectureChapter(BaseModel):
    """A semantic chapter within a lecture, derived from `video_chapters`.

    Optional in `Lecture.chapters`: when present, the router can route at
    chapter granularity ("the part of L2 where TCP handshake is discussed")
    instead of just at the lecture level.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chapter_index: int
    title: str
    description: str | None = None
    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return max(0.0, float(self.end_sec) - float(self.start_sec))


class Lecture(BaseModel):
    """A single lecture in a course.

    Backed by a `VideoAsset` row joined with its `CourseContent` row.
    `id` is the `VideoAsset.id` (the canonical key for transcript / chapters /
    AI summary lookups). `content_id` mirrors `CourseContent.id` and is used
    for in-app links (VideoPlayer page, download redirects, etc.).

    `slug` is a stable per-turn token (e.g. "L1", "L2") that we expose to the
    LLM in place of the UUID. The endpoint resolves a slug back to a Lecture
    via `CourseInfo.lecture_by_slug`. Slugs are assigned by the builder when
    the `CourseInfo` is constructed; treat them as opaque from the model's
    perspective.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content_id: UUID | None = None

    slug: str = Field(
        description="Stable per-turn token exposed to the LLM in place of the UUID (e.g. 'L1').",
    )

    title: str
    description: str | None = None

    summary: str | None = Field(
        default=None,
        description="Stored long-form AI summary (typically `VideoAsset.ai_summary`).",
    )

    duration_sec: float | None = None

    transcript_ready: bool = Field(
        default=False,
        description="True when transcript ingestion has completed; routers should prefer ready lectures.",
    )

    chapters: list[LectureChapter] = Field(default_factory=list)

    def to_prompt_string(
        self,
        *,
        include_chapters: bool = True,
        include_summary: bool = True,
        summary_max_chars: int = 1200,
    ) -> str:
        lines: list[str] = []
        head = f"[{self.slug}] {self.title}"
        if self.duration_sec is not None:
            head += f" (~{_fmt_timestamp(self.duration_sec)})"
        if not self.transcript_ready:
            head += "  (transcript not ready)"
        lines.append(head)

        if self.description:
            desc = self.description.strip()
            if desc:
                lines.append(f"  description: {desc}")

        if include_summary and self.summary:
            summary = self.summary.strip()
            if summary:
                if len(summary) > summary_max_chars:
                    summary = summary[:summary_max_chars].rstrip() + "…"
                lines.append(f"  summary: {summary}")

        if include_chapters and self.chapters:
            lines.append("  chapters:")
            for ch in self.chapters:
                lines.append(
                    f"    - {_fmt_timestamp(ch.start_sec)}–{_fmt_timestamp(ch.end_sec)} {ch.title}"
                )

        return "\n".join(lines)


class CourseInfo(BaseModel):
    """Compact, AI-ready snapshot of a course.

    Built once per chat turn and reused across every DSPy signature, so we
    pay the DB cost exactly once per `forward()` pass and every sub-signature
    sees an identical view of the world.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None

    summary: str | None = Field(
        default=None,
        description=(
            "Course-level summary. Not yet persisted in the DB (no column on `courses`); "
            "placeholder for future use — either a stored AI summary or one synthesized from "
            "lecture summaries at build time."
        ),
    )

    lectures: list[Lecture] = Field(default_factory=list)

    def lecture_by_slug(self, slug: str) -> Lecture | None:
        norm = (slug or "").strip()
        if not norm:
            return None
        for lec in self.lectures:
            if lec.slug == norm:
                return lec
        norm_upper = norm.upper()
        for lec in self.lectures:
            if lec.slug.upper() == norm_upper:
                return lec
        return None

    def lectures_by_slugs(self, slugs: list[str]) -> list[Lecture]:
        seen: set[str] = set()
        out: list[Lecture] = []
        for s in slugs or []:
            lec = self.lecture_by_slug(s)
            if lec is None or lec.slug in seen:
                continue
            seen.add(lec.slug)
            out.append(lec)
        return out

    def to_prompt_string(
        self,
        *,
        include_chapters: bool = True,
        include_lecture_summaries: bool = True,
        summary_max_chars: int = 1200,
        max_chars: int = 8000,
    ) -> str:
        """Compact prompt rendering. Truncates from the bottom if `max_chars` is exceeded.

        The shape is intentionally stable so every DSPy signature that accepts
        `course_info` sees the same format and can be optimized against it.
        """
        lines: list[str] = [f"Course: {self.name}"]
        if self.description:
            desc = self.description.strip()
            if desc:
                lines.append(f"Description: {desc}")
        if self.summary:
            summary = self.summary.strip()
            if summary:
                if len(summary) > summary_max_chars:
                    summary = summary[:summary_max_chars].rstrip() + "…"
                lines.append(f"Course summary: {summary}")

        if self.lectures:
            lines.append("")
            lines.append("Lectures:")
            for lec in self.lectures:
                lines.append(
                    lec.to_prompt_string(
                        include_chapters=include_chapters,
                        include_summary=include_lecture_summaries,
                        summary_max_chars=summary_max_chars,
                    )
                )

        text = "\n".join(lines).strip()
        if max_chars and len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n…(truncated)"
        return text
