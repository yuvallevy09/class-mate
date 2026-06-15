from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Lecture(BaseModel):
    """A single lecture in a course.

    Backed by a `VideoAsset` row joined with its `CourseContent` row.
    `id` is the `VideoAsset.id` (the canonical key for transcript / chapter /
    AI summary lookups). `content_id` mirrors `CourseContent.id` and is used
    for in-app links (VideoPlayer page, download redirects, etc.).

    `slug` is a stable per-turn token (e.g. "L1", "L2") that we expose to the
    LLM in place of the UUID. The endpoint resolves a slug back to a Lecture
    via `CourseInfo.lecture_by_slug`. Slugs are assigned by the builder when
    the `CourseInfo` is constructed; treat them as opaque from the model's
    perspective.

    The three text fields are deliberately distinct sources and feed different
    prompt views (see `CourseInfo.to_basic_info` / `to_detailed_info`):
    - `description` — instructor-authored blurb (`CourseContent.description`).
    - `ai_description` — short generated one-liner (`VideoAsset.ai_description`),
      the headline used in the lean catalog view.
    - `summary` — long generated summary (`VideoAsset.ai_summary`), used in the
      rich planning view that drives lecture routing.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content_id: UUID | None = None

    slug: str = Field(
        description="Stable per-turn token exposed to the LLM in place of the UUID (e.g. 'L1').",
    )

    title: str
    description: str | None = None

    ai_description: str | None = Field(
        default=None,
        description="Short generated one-liner (typically `VideoAsset.ai_description`).",
    )

    summary: str | None = Field(
        default=None,
        description="Stored long-form AI summary (typically `VideoAsset.ai_summary`).",
    )

    transcript_ready: bool = Field(
        default=False,
        description="True when transcript ingestion has completed; routers should prefer ready lectures.",
    )

    # `slug: title` (no brackets) keeps the slug out of the bracketed `[N]`
    # citation namespace used by `retrieved_docs` in `AnswerFromContext` — models
    # were grabbing `[L1]` as a citation key, which the digit-only regex in
    # `_format_reply_with_citation_links` couldn't rewrite.
    def _headline(self) -> str:
        head = f"{self.slug}: {self.title}"
        if not self.transcript_ready:
            head += "  (transcript not ready)"
        return head

    def _catalog_line(self) -> str:
        """One compact line: headline + a short one-liner when available.

        Prefers the generated `ai_description`; falls back to the instructor
        `description`. Used by the lean catalog view (`to_basic_info`).
        """
        head = self._headline()
        blurb = (self.ai_description or self.description or "").strip()
        if blurb:
            head += f" — {blurb}"
        return head

    def _detailed_block(self, *, summary_max_chars: int = 1200) -> str:
        """Headline + full summary on an indented line.

        Prefers the long `summary`; falls back to `ai_description` then the
        instructor `description`. Used by the rich planning view
        (`to_detailed_info`) that drives lecture routing.
        """
        lines = [self._headline()]
        body = (self.summary or self.ai_description or self.description or "").strip()
        if body:
            if len(body) > summary_max_chars:
                body = body[:summary_max_chars].rstrip() + "…"
            lines.append(f"  summary: {body}")
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

    def lecture_by_id(self, video_asset_id: UUID) -> Lecture | None:
        """Resolve a lecture by its `VideoAsset.id`.

        Used to turn the player's `watching_video_asset_id` into a `Lecture`
        (and thence a slug) so viewing context can be scoped/anchored. Mirrors
        `lecture_by_slug`; returns None when the id isn't part of this course.
        """
        for lec in self.lectures:
            if lec.id == video_asset_id:
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

    # --- Prompt views ---
    #
    # Three nested projections, each tailored to a signature's job (see
    # `TeachingAssistant`). Increasing detail: header ⊂ basic_info ⊂ detailed_info.
    # Naming by content (not by consumer) since `basic_info` is shared by several
    # signatures. All are pure string renderers — no I/O.

    def _course_header_lines(
        self, *, include_summary: bool, summary_max_chars: int
    ) -> list[str]:
        lines: list[str] = [f"Course: {self.name}"]
        if self.description and self.description.strip():
            lines.append(f"Description: {self.description.strip()}")
        if include_summary and self.summary and self.summary.strip():
            s = self.summary.strip()
            if summary_max_chars and len(s) > summary_max_chars:
                s = s[:summary_max_chars].rstrip() + "…"
            lines.append(f"Course summary: {s}")
        return lines

    def to_header(self) -> str:
        """Minimal identity view: course name + instructor description only.

        Tone/scope only — no lecture catalog, no AI summaries. Used by
        `AnswerFromContext`, where the substantive content comes from the
        retrieved docs and any catalog/summary text would invite uncited claims.
        """
        return "\n".join(
            self._course_header_lines(include_summary=False, summary_max_chars=0)
        ).strip()

    def to_basic_info(self) -> str:
        """Lean catalog view: course header + a one-line entry per lecture
        (slug, title, transcript-ready flag, short one-liner).

        Used by the router, clarifier, and no-context fallback — enough to judge
        relevance and redirect, without the full summaries that would tempt the
        model to answer from the catalog instead of retrieving.
        """
        lines = self._course_header_lines(include_summary=True, summary_max_chars=600)
        if self.lectures:
            lines.append("")
            lines.append("Lectures:")
            lines.extend(lec._catalog_line() for lec in self.lectures)
        return "\n".join(lines).strip()

    def to_detailed_info(self, *, summary_budget: int = 12_000) -> str:
        """Rich planning view: course header + each lecture with its full summary.

        This is the view that drives lecture routing in `GenerateRetrievalDetails`,
        so it spends the tokens. Per-lecture summary length scales DOWN with the
        lecture count to keep the prompt bounded *without dropping later lectures*
        — every slug stays visible (the old bottom-truncation could silently make
        late lectures invisible to routing).
        """
        lines = self._course_header_lines(include_summary=True, summary_max_chars=1200)
        if self.lectures:
            per_lecture = max(200, int(summary_budget) // len(self.lectures))
            lines.append("")
            lines.append("Lectures:")
            lines.extend(
                lec._detailed_block(summary_max_chars=per_lecture)
                for lec in self.lectures
            )
        return "\n".join(lines).strip()
