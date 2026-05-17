"""Cascading retrieval strategy for the new RAG pipeline.

Composes the three lecture-scoped retrievers (explicit / full-lecture / hybrid)
into a single async entry point. Returns a `RetrievalDecision(docs, path)` so
callers can both feed the LLM and emit per-turn analytics about which path
fired.

Keeping the cascade here (rather than inside `TeachingAssistant`) means the
DSPy module stays string-in / string-out and easy to unit-test with a mock
retriever.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.explicit_retrieve import retrieve_explicitly
from app.rag.hybrid_retrieve import HybridRetrieveConfig, retrieve_course_hybrid_hits
from app.rag.lecture_text import (
    DEFAULT_FULL_TRANSCRIPT_CHAR_BUDGET,
    get_lecture_text,
)
from app.rag.types import RagHit
from app.schemas.course_info import CourseInfo, Lecture
from app.schemas.retrieval import RetrievedDoc


RetrievalPath = Literal["explicit", "full_lecture", "hybrid", "none"]


@dataclass(frozen=True)
class RetrievalDecision:
    """Output of one cascade run.

    `docs` is empty exactly when `path == 'none'` — the caller can branch on
    either to surface the "no context found" terminal state.
    """

    docs: list[RetrievedDoc]
    path: RetrievalPath


def _hits_to_docs(
    *,
    hits: list[RagHit],
    course_info: CourseInfo,
    lecture_filter: set[UUID] | None,
) -> list[RetrievedDoc]:
    """Adapt hybrid `RagHit`s into `RetrievedDoc`s using `course_info` for slug/title.

    - Non-video hits (no `video_asset_id` in metadata) are skipped: `RetrievedDoc`
      is video-centric (`lecture_id` is required). The new pipeline is lecture-
      scoped; non-video corpora can be added later by relaxing the schema.
    - When `lecture_filter` is set, post-filters to hits whose `video_asset_id`
      is in the filter set. Pass `None` for an unfiltered (course-wide) view.
    """
    by_id: dict[UUID, Lecture] = {lec.id: lec for lec in course_info.lectures}

    def _parse_float(v) -> float | None:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _parse_uuid(v) -> UUID | None:
        if v is None:
            return None
        try:
            return UUID(str(v))
        except (TypeError, ValueError):
            return None

    out: list[RetrievedDoc] = []
    for h in hits:
        meta = h.metadata or {}
        asset_id = _parse_uuid(meta.get("video_asset_id"))
        if asset_id is None:
            continue
        if lecture_filter is not None and asset_id not in lecture_filter:
            continue
        lec = by_id.get(asset_id)
        if lec is None:
            continue
        out.append(
            RetrievedDoc(
                text=h.text or "",
                lecture_id=lec.id,
                lecture_slug=lec.slug,
                lecture_title=lec.title,
                content_id=lec.content_id,
                chapter_id=_parse_uuid(meta.get("chapter_id")),
                chapter_title=None,  # endpoint enriches via `_attach_video_chapter_titles`
                start_sec=_parse_float(meta.get("start_sec")),
                end_sec=_parse_float(meta.get("end_sec")),
            )
        )
    return out


async def _perform_hybrid_search(
    *,
    db: AsyncSession,
    course_info: CourseInfo,
    query: str,
    lecture_slugs: list[str] | None,
    cfg: HybridRetrieveConfig | None = None,
) -> list[RetrievedDoc]:
    """Run BM25+pgvector+RRF over the course and adapt to `RetrievedDoc`.

    When `lecture_slugs` is non-empty, post-filters hits by `video_asset_id`.
    When empty/None, returns the unfiltered hybrid view (course-wide).
    If `lecture_slugs` is non-empty but none resolve to a ready lecture, returns
    `[]` — we'd rather surface "no context" than silently widen to the whole
    course after the model explicitly scoped its request.
    """
    q = (query or "").strip()
    if not q:
        return []

    lecture_filter: set[UUID] | None = None
    if lecture_slugs:
        lectures = course_info.lectures_by_slugs(lecture_slugs)
        lecture_filter = {lec.id for lec in lectures if lec.transcript_ready}
        if not lecture_filter:
            return []

    hits = await retrieve_course_hybrid_hits(
        db=db,
        course_id=course_info.id,
        query=q,
        cfg=cfg or HybridRetrieveConfig(),
        categories=None,
    )
    return _hits_to_docs(
        hits=list(hits),
        course_info=course_info,
        lecture_filter=lecture_filter,
    )


class CourseRetriever:
    """Cascading retrieval orchestrator.

    Cascade:
      1. If `target_lecture_slug` + `target_timestamp` are present, try
         `retrieve_explicitly`. Return on success (path='explicit').
      2. Compute the routing slug set: prefer `lecture_routing`, but ensure
         `target_lecture_slug` is included as a fallback when the model only
         emitted a target (and explicit retrieval just failed).
      3. Try `get_lecture_text` with that slug set. If it returns docs AND the
         combined size is under budget, return them (path='full_lecture').
      4. Else run hybrid search scoped to the same slug set, or course-wide
         when the set is empty (path='hybrid').
      5. If nothing turned up, return `RetrievalDecision(docs=[], path='none')`
         so the caller can switch to an honest "I couldn't find that" answer.

    Stateless across calls; safe to share between requests.
    """

    def __init__(
        self,
        *,
        full_transcript_char_budget: int = DEFAULT_FULL_TRANSCRIPT_CHAR_BUDGET,
        hybrid_config: HybridRetrieveConfig | None = None,
    ) -> None:
        self._budget = int(full_transcript_char_budget)
        self._hybrid_cfg = hybrid_config

    async def retrieve(
        self,
        *,
        db: AsyncSession,
        course_info: CourseInfo,
        contextualized_query: str,
        lecture_routing: list[str] | None,
        target_lecture_slug: str | None,
        target_timestamp: str | None,
    ) -> RetrievalDecision:
        target_slug = (target_lecture_slug or "").strip() or None
        target_ts = (target_timestamp or "").strip() or None

        if target_slug and target_ts:
            docs = await retrieve_explicitly(
                db=db,
                course_info=course_info,
                lecture_slug=target_slug,
                timestamp=target_ts,
            )
            if docs:
                return RetrievalDecision(docs=docs, path="explicit")

        routing = [s for s in (lecture_routing or []) if isinstance(s, str) and s.strip()]
        if target_slug and target_slug not in routing:
            routing.insert(0, target_slug)

        full_docs, is_long = await get_lecture_text(
            db=db,
            course_info=course_info,
            lecture_slugs=routing,
            char_budget=self._budget,
        )
        if full_docs and not is_long:
            return RetrievalDecision(docs=full_docs, path="full_lecture")

        hybrid_docs = await _perform_hybrid_search(
            db=db,
            course_info=course_info,
            query=contextualized_query,
            lecture_slugs=routing,
            cfg=self._hybrid_cfg,
        )
        if hybrid_docs:
            return RetrievalDecision(docs=hybrid_docs, path="hybrid")

        return RetrievalDecision(docs=[], path="none")
