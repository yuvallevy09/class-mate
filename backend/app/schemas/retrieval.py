from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def _fmt_timestamp(seconds: float | int | None) -> str:
    """Render seconds as `M:SS` (or `H:MM:SS` past one hour). Kept local so this
    module stays self-contained for the retrieval layer."""
    try:
        s = max(0.0, float(seconds or 0.0))
    except (TypeError, ValueError):
        s = 0.0
    hh = int(s // 3600)
    mm = int((s % 3600) // 60)
    ss = int(s % 60)
    if hh > 0:
        return f"{hh}:{mm:02d}:{ss:02d}"
    return f"{mm}:{ss:02d}"


class RetrievedDoc(BaseModel):
    """A single piece of context returned by a retrieval strategy.

    The `text` field is what the LLM consumes. The remaining fields carry the
    metadata we need server-side to (a) display a meaningful source label and
    (b) later build a `ChatCitation` without re-querying the DB. Keeping these
    structured all the way through is what lets in-app `/VideoPlayer?t=...`
    links and chapter-title enrichment keep working in the new pipeline.
    """

    model_config = ConfigDict(from_attributes=True)

    text: str = Field(description="The content the LLM should read, with optional inline timestamp markers.")

    lecture_id: UUID = Field(description="VideoAsset.id (canonical key for transcript/chapters/summary).")
    lecture_slug: str = Field(description="Per-turn slug from CourseInfo (e.g. 'L2').")
    lecture_title: str

    content_id: UUID | None = Field(
        default=None,
        description="CourseContent.id (used to build in-app /VideoPlayer links).",
    )

    chapter_id: UUID | None = None
    chapter_title: str | None = None

    start_sec: float | None = None
    end_sec: float | None = None

    @property
    def source_label(self) -> str:
        parts: list[str] = [self.lecture_title.strip() or self.lecture_slug]
        if self.chapter_title:
            parts.append(self.chapter_title.strip())
        time_label = ""
        if self.start_sec is not None and self.end_sec is not None:
            time_label = f" ({_fmt_timestamp(self.start_sec)}–{_fmt_timestamp(self.end_sec)})"
        elif self.start_sec is not None:
            time_label = f" ({_fmt_timestamp(self.start_sec)})"
        return " · ".join(parts) + time_label

    def to_prompt_string(self, *, index: int | None = None) -> str:
        """Compact rendering for inclusion as one element in `list[str] retrieved_docs`.

        When `index` is supplied, prefixes the rendering with `[N]` so the LLM
        has a stable citation key it can echo back inline (`...as discussed [1]`).
        The endpoint reuses this same 1-based ordering when building
        `ChatCitation`s, so the post-processor in
        `_format_reply_with_citation_links` can rewrite `[N]` into a clickable
        superscript that anchors to the matching Sources entry.
        """
        prefix = f"[{int(index)}] " if index is not None else ""
        return f"{prefix}[Source: {self.source_label}]\n{self.text}".strip()
