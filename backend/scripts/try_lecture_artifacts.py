"""Replay the lecture-artifact DSPy module on real transcripts from the local DB.

Read-only with respect to the database: builds the exact transcript context the
production pipeline would build, runs `LectureArtifactGenerator`, and prints the
results — nothing is persisted to `video_assets`. Use it to judge output quality
and to iterate on the prompt (edit the `SummarizeLecture` signature in
`app/ai/lecture_artifact_generator.py`, rerun, compare).

When DSPY_TRACING_ENABLED is set, each replay is also recorded as an MLflow
trace (same store as the server: `mlflow ui --backend-store-uri sqlite:///mlflow.db`).

Usage (from backend/):
    uv run python scripts/try_lecture_artifacts.py                 # list lectures
    uv run python scripts/try_lecture_artifacts.py serverless      # run on title match
    uv run python scripts/try_lecture_artifacts.py <video-asset-uuid>
    uv run python scripts/try_lecture_artifacts.py serverless --out report.md
    uv run python scripts/try_lecture_artifacts.py serverless --show-transcript
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ — allow `python scripts/...`

from app.ai.lecture_artifact_generator import LectureArtifacts, get_lecture_artifact_generator
from app.core.settings import get_settings
from app.core.tracing import setup_dspy_tracing
from app.db.models.course import Course  # noqa: F401 - mapper registry needs Course<->User resolved
from app.db.models.course_content import CourseContent
from app.db.models.transcript_segment import TranscriptSegment
from app.db.models.user import User  # noqa: F401 - mapper registry needs Course<->User resolved
from app.db.models.video_asset import VideoAsset
from app.services.lecture_artifacts import _TRANSCRIPT_MARKER, _build_lecture_transcript


@dataclass
class LectureRow:
    asset: VideoAsset
    content_title: str | None
    segment_count: int


async def _load_lectures(session: AsyncSession) -> list[LectureRow]:
    seg_counts = (
        select(
            TranscriptSegment.video_asset_id,
            func.count(TranscriptSegment.id).label("n"),
        )
        .group_by(TranscriptSegment.video_asset_id)
        .subquery()
    )
    res = await session.execute(
        select(VideoAsset, CourseContent.title, func.coalesce(seg_counts.c.n, 0))
        .join(CourseContent, CourseContent.id == VideoAsset.content_id, isouter=True)
        .join(seg_counts, seg_counts.c.video_asset_id == VideoAsset.id, isouter=True)
        .order_by(VideoAsset.created_at.asc())
    )
    return [
        LectureRow(asset=asset, content_title=title, segment_count=int(n))
        for asset, title, n in res.all()
    ]


def _display_name(row: LectureRow) -> str:
    return (
        (row.content_title or "").strip()
        or (row.asset.ai_title or "").strip()
        or (row.asset.original_filename or "").strip()
        or str(row.asset.id)
    )


def _print_listing(rows: list[LectureRow]) -> None:
    if not rows:
        print("No video assets in the database.")
        return
    print(f"{len(rows)} video asset(s):\n")
    for row in rows:
        ready = "ready" if row.segment_count else "NO TRANSCRIPT"
        print(f"  {row.asset.id}  [{ready}, {row.segment_count} segments]")
        print(f"      title:    {_display_name(row)}")
        if row.asset.ai_title:
            print(f"      ai_title: {row.asset.ai_title}")
        print()


def _match(rows: list[LectureRow], query: str) -> list[LectureRow]:
    try:
        asset_id = UUID(query)
        return [r for r in rows if r.asset.id == asset_id]
    except ValueError:
        pass
    q = query.lower()
    return [
        r
        for r in rows
        if q in (r.content_title or "").lower()
        or q in (r.asset.ai_title or "").lower()
        or q in (r.asset.original_filename or "").lower()
    ]


def _render_report(
    *, row: LectureRow, transcript: str, artifacts: LectureArtifacts, started_at: datetime
) -> str:
    reasoning = artifacts.debug.get("reasoning", "")
    parts = [
        f"# Lecture artifact replay — {_display_name(row)}",
        "",
        f"- video_asset_id: `{row.asset.id}`",
        f"- run at: {started_at.isoformat(timespec='seconds')}",
        f"- transcript: {row.segment_count} segments, {len(transcript)} chars",
        "",
        "## generated_title",
        "",
        artifacts.generated_title,
        "",
        "## generated_desc",
        "",
        artifacts.generated_desc,
        "",
        "## generated_summary",
        "",
        artifacts.generated_summary,
        "",
        "## reasoning (ChainOfThought, not user-facing)",
        "",
        reasoning or "(empty)",
        "",
        "## input transcript",
        "",
        "```",
        transcript,
        "```",
        "",
    ]
    return "\n".join(parts)


def _print_results(transcript: str, artifacts: LectureArtifacts, *, show_transcript: bool) -> None:
    bar = "=" * 70
    if show_transcript:
        print(bar)
        print("INPUT TRANSCRIPT")
        print(bar)
        print(transcript)
        print()
    reasoning = artifacts.debug.get("reasoning", "")
    if reasoning:
        print(bar)
        print("REASONING (ChainOfThought, not user-facing)")
        print(bar)
        print(reasoning)
        print()
    print(bar)
    print("GENERATED TITLE")
    print(bar)
    print(artifacts.generated_title)
    print()
    print(bar)
    print("GENERATED DESCRIPTION")
    print(bar)
    print(artifacts.generated_desc)
    print()
    print(bar)
    print("GENERATED SUMMARY")
    print(bar)
    print(artifacts.generated_summary)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "query",
        nargs="?",
        help="video_asset UUID or a (partial, case-insensitive) lecture title; omit to list",
    )
    parser.add_argument("--out", type=Path, help="also write a markdown report to this file")
    parser.add_argument(
        "--show-transcript",
        action="store_true",
        help="print the full transcript input before the outputs",
    )
    args = parser.parse_args()

    settings = get_settings()
    if settings.dspy_tracing_enabled:
        setup_dspy_tracing()
    # LiteLLM reads the Gemini key from the environment; export it from settings so
    # the script works in a shell where only backend/.env has the key.
    if settings.google_api_key:
        os.environ.setdefault("GEMINI_API_KEY", settings.google_api_key)
        os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            rows = await _load_lectures(session)

            if not args.query:
                _print_listing(rows)
                return 0

            matches = _match(rows, args.query)
            if not matches:
                print(f"No video asset matches {args.query!r}. Available:")
                _print_listing(rows)
                return 1
            if len(matches) > 1:
                print(f"{args.query!r} is ambiguous; matches:")
                _print_listing(matches)
                return 1

            row = matches[0]
            if not row.segment_count:
                print(f"'{_display_name(row)}' has no transcript segments yet.")
                return 1

            transcript = await _build_lecture_transcript(
                db=session,
                course_id=row.asset.course_id,
                content_id=row.asset.content_id,
                video_asset_id=row.asset.id,
            )
            if _TRANSCRIPT_MARKER not in transcript:
                print(f"Transcript for '{_display_name(row)}' is empty.")
                return 1

        print(
            f"Running LectureArtifactGenerator on '{_display_name(row)}' "
            f"({row.segment_count} segments, {len(transcript)} chars; model "
            f"{settings.gemini_model})...\n"
        )
        started_at = datetime.now().astimezone()
        artifacts = await get_lecture_artifact_generator().aforward(lecture_transcript=transcript)

        _print_results(transcript, artifacts, show_transcript=args.show_transcript)

        if args.out:
            args.out.write_text(
                _render_report(
                    row=row, transcript=transcript, artifacts=artifacts, started_at=started_at
                ),
                encoding="utf-8",
            )
            print(f"\nReport written to {args.out}")
        if settings.dspy_tracing_enabled:
            print("\n(Trace recorded — `mlflow ui --backend-store-uri sqlite:///mlflow.db`)")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
