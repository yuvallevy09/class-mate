from __future__ import annotations

from uuid import uuid4

from app.db.models.transcript_segment import TranscriptSegment
from app.services.video_chapters import _build_blocks_from_segments


def test_build_blocks_merges_into_coarse_windows() -> None:
    vid = uuid4()
    segs = []
    # 12 segments of 5s each => 60s total
    for i in range(12):
        segs.append(
            TranscriptSegment(
                course_id=uuid4(),
                video_asset_id=vid,
                start_sec=float(i * 5),
                end_sec=float((i + 1) * 5),
                text=f"seg{i}",
                language_code="en",
            )
        )

    blocks = _build_blocks_from_segments(segments=segs, target_block_seconds=20.0, max_block_seconds=30.0)
    assert blocks
    # Should produce multiple blocks, roughly ~20s each.
    assert len(blocks) >= 2
    for b in blocks:
        assert b.end_sec >= b.start_sec
        assert b.text

