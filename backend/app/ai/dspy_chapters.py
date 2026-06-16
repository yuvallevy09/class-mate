from __future__ import annotations

import json
from typing import Any

import dspy

from app.ai.model_roles import CHAPTERS, build_lm_for_role
from app.core.settings import Settings


class _ChapterizeSig(dspy.Signature):
    """
    Segment a lecture into semantic chapters.

    Output MUST be strict JSON with this shape:
    {
      "chapters": [
        {"start_block": 0, "end_block": 4, "title": "...", "description": "..."},
        ...
      ]
    }
    """

    blocks: str = dspy.InputField(
        desc=(
            "Ordered transcript blocks. Each line contains: "
            "[b<idx>] <start_sec>-<end_sec> <text>"
        )
    )
    chapters_json: str = dspy.OutputField(
        desc=(
            "Strict JSON only. No markdown. Must include 1..N chapters with integer "
            "start_block/end_block (inclusive) and short title/description."
        )
    )


def generate_chapters_dspy(*, settings: Settings, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Call DSPy to produce semantic chapter boundaries over transcript blocks.

    Raises ValueError if not configured (missing key). Other errors may propagate.
    """
    lm = build_lm_for_role(settings, CHAPTERS, temperature=0.0, max_tokens=700)
    pred = dspy.Predict(_ChapterizeSig)

    # Compact representation to keep token usage bounded.
    lines: list[str] = []
    for b in blocks:
        i = int(b.get("block_index", 0))
        s = float(b.get("start_sec", 0.0))
        e = float(b.get("end_sec", 0.0))
        t = str(b.get("text", "") or "").strip().replace("\n", " ")
        lines.append(f"[b{i}] {s:.1f}-{e:.1f} {t}")

    prompt_blocks = "\n".join(lines).strip()
    with dspy.settings.context(lm=lm):
        out = pred(blocks=prompt_blocks)

    raw = (getattr(out, "chapters_json", "") or "").strip()
    # Be tolerant of accidental leading/trailing text: extract first JSON object.
    try:
        return json.loads(raw)
    except Exception:
        # Try to salvage if model included extra text.
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise

