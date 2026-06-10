"""DSPy module for generating per-lecture AI artifacts.

Given one lecture's (timestamped) transcript, produces a structured triple:

    generated_title  -> 2–5 word lecture title
    generated_desc   -> 1–3 sentence description
    generated_summary -> Markdown summary with `[#M:SS]` timestamp citations

This is the structured replacement for the legacy `video_summary.generate_*`
path (which asked the v1 `ChatEngine` for free-form JSON and parsed it by hand).
Mirroring `TeachingAssistant`, the DSPy module stays string-in / string-out and
returns a pydantic result so the service layer can persist fields and the
endpoint can serialize them.

Design notes:

- The summary keeps the EXACT `[#M:SS]` bracket format the frontend linkifier
  (`linkifySummaryTimestamps` in `VideoPlayer.jsx`) depends on. That regex only
  matches `M:SS` (1–3 digit minutes), so `_normalize_summary_timestamps` repairs
  common deviations (missing `#`, `H:MM:SS` form) into total-minutes form.
- DSPy does blocking HTTP I/O; `aforward` wraps the call in `asyncio.to_thread`
  so the FastAPI event loop stays responsive.
- The LM is built lazily from `Settings` and reused via the process-level
  singleton. Tests can inject a pre-built `lm`.
"""

from __future__ import annotations

import asyncio
import re
from functools import lru_cache
from typing import Any

import dspy
from pydantic import BaseModel, Field

from app.ai.dspy_chat import _effective_gemini_api_key, _litellm_gemini_model
from app.core.settings import Settings, get_settings


# ChainOfThought adds a `reasoning` output that shares this budget with the three
# artifact fields. A long full-transcript summary needs headroom so it isn't
# truncated mid-field (which would break structured parsing). gemini-2.5-flash
# supports far more than this; the value is a safe upper bound for dev.
_DEFAULT_MAX_TOKENS = 12_288


# --- Signature ---


class SummarizeLecture(dspy.Signature):
    """You are a teaching assistant that is uploading helpful material for student to follow along their course. Generate an appropriate and accurate title, description, and summary for the students given the lecture transcript."""

    lecture_transcript: str = dspy.InputField(
        desc="Timestamped transcript excerpt for this lecture."
    )

    generated_title: str = dspy.OutputField(
        desc="2–5 word title for the lecture."
    )
    generated_desc: str = dspy.OutputField(
        desc="1–3 sentences describing what this lecture covers."
    )
    generated_summary: str = dspy.OutputField(
        desc=(
            "A summary of the lecture in Markdown structure:\n"
            "- Use short section headings and bullet points; lead with the main topics, "
            "then key concepts and takeaways.\n"
            "- Be faithful to the transcript only. Do not invent content, and do not pad with filler.\n\n"
            "Timestamp Citations:\n"
            "1. Anchor key moments to timestamps that actually appear in the transcript.\n"
            "2. Use EXACTLY this bracket format: '[#M:SS]', or a comma-separated list "
            "like '[#0:04, #6:06, #15:15]'.\n"
            "3. For a moment at 1h15m20s write '[#75:20]', not '[#1:15:20]'.\n"
            "4. Do not write bare timestamps like '(0:04)' or '#0:04' without the square "
            "brackets, and do not wrap them in markdown links yourself.\n"
            "5. Only cite timestamps present in the transcript; do not fabricate them."
        )
    )


# --- Result ---


class LectureArtifacts(BaseModel):
    """Structured, post-processed output for one lecture.

    `debug` carries non-user-facing diagnostics (the `ChainOfThought` reasoning
    string). The service layer persists the three artifact fields and may log
    `debug` in development.
    """

    generated_title: str
    generated_desc: str
    generated_summary: str
    debug: dict[str, Any] = Field(default_factory=dict)


# --- Post-processing ---

# Matches a bracket group that contains at least one timestamp-like token. We
# only rewrite brackets whose contents are *purely* timestamps (see `_repl`), so
# numeric citations like `[1]` or metadata like `[Source: ...]` are left alone.
_TIMESTAMP_BRACKET_RE = re.compile(r"\[([^\[\]\n]*\d{1,3}:\d{2}[^\[\]\n]*)\]")

# Defensive: strip an echoed schema/label prefix the LM sometimes prepends,
# e.g. "JSON Title: ..." or "Title - ...".
_TITLE_JUNK_PREFIX_RE = re.compile(r"^(json\s+)?title\s*[:\-]\s*", re.IGNORECASE)

_MAX_TITLE_WORDS = 5
_MAX_TITLE_CHARS = 120
_MAX_DESC_CHARS = 600


def _ts_to_total_minutes(token: str) -> str | None:
    """Coerce a `M:SS` or `H:MM:SS` token into canonical total-minutes `M:SS`.

    Returns None for anything that isn't a valid timestamp so the caller can
    leave the surrounding text untouched. `H:MM:SS` is folded into total minutes
    (e.g. '1:15:20' -> '75:20') to match the frontend linkifier, which only
    understands the `M:SS` form.
    """
    parts = token.split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    if len(parts) == 2:
        minutes, seconds = nums
    else:
        hours, minutes, seconds = nums
        if minutes >= 60:
            return None
        minutes += hours * 60
    if seconds >= 60:
        return None
    return f"{minutes}:{seconds:02d}"


def _normalize_summary_timestamps(text: str) -> str:
    """Repair timestamp brackets into the canonical `[#M:SS]` form.

    Handles the common LM deviations from the prompt contract:
      - missing '#'         `[0:04]`        -> `[#0:04]`
      - hour form           `[#1:15:20]`    -> `[#75:20]`
      - mixed lists         `[0:04, #6:06]` -> `[#0:04, #6:06]`

    A bracket is rewritten only when *every* comma-separated token is a valid
    timestamp; otherwise the whole bracket is left as-is. Bare (unbracketed)
    timestamps are intentionally not touched to avoid false positives in prose.
    """
    if not text:
        return text

    def _repl(m: re.Match) -> str:
        inside = m.group(1)
        out_tokens: list[str] = []
        for raw in inside.split(","):
            token = raw.strip().lstrip("#").strip()
            canon = _ts_to_total_minutes(token)
            if canon is None:
                return m.group(0)  # not a pure timestamp bracket -> leave untouched
            out_tokens.append(f"#{canon}")
        if not out_tokens:
            return m.group(0)
        return "[" + ", ".join(out_tokens) + "]"

    return _TIMESTAMP_BRACKET_RE.sub(_repl, text)


def _clean_title(raw: str) -> str:
    """Normalize the generated title: strip quotes/labels, cap to 5 words."""
    t = (raw or "").strip().strip("\"'`“”‘’").strip()
    t = _TITLE_JUNK_PREFIX_RE.sub("", t).strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[.?!:;,\-–—]+$", "", t).strip()
    words = t.split()
    if len(words) > _MAX_TITLE_WORDS:
        t = " ".join(words[:_MAX_TITLE_WORDS])
    return t[:_MAX_TITLE_CHARS].strip()


def _clean_desc(raw: str) -> str:
    """Normalize the generated description: collapse whitespace, cap length."""
    d = re.sub(r"\s+", " ", (raw or "").strip())
    return d[:_MAX_DESC_CHARS].strip()


# --- Module ---


class LectureArtifactGenerator(dspy.Module):
    """DSPy orchestrator for one lecture's AI artifacts.

    Stateless after construction; safe to share across requests via the
    process-level singleton. Pass a pre-built `lm` (e.g. a DSPy `DummyLM`) to
    short-circuit LM construction in tests.
    """

    def __init__(self, *, lm: dspy.LM | None = None) -> None:
        super().__init__()
        self._lm: dspy.LM | None = lm
        self.summarizer = dspy.ChainOfThought(SummarizeLecture)

    def _get_lm(self) -> dspy.LM:
        if self._lm is None:
            settings: Settings = get_settings()
            _effective_gemini_api_key(settings)  # fails fast if the key is missing
            self._lm = dspy.LM(
                model=_litellm_gemini_model(settings.gemini_model),
                temperature=float(settings.chat_temperature),
                max_tokens=_DEFAULT_MAX_TOKENS,
                num_retries=2,
            )
        return self._lm

    async def aforward(self, *, lecture_transcript: str) -> LectureArtifacts:
        transcript = (lecture_transcript or "").strip()
        if not transcript:
            raise ValueError("lecture_transcript must be non-empty")

        pred = await asyncio.to_thread(self._summarize_raw, lecture_transcript=transcript)

        debug: dict[str, Any] = {}
        reasoning = str(getattr(pred, "reasoning", "") or "").strip()
        if reasoning:
            debug["reasoning"] = reasoning

        return LectureArtifacts(
            generated_title=_clean_title(str(getattr(pred, "generated_title", "") or "")),
            generated_desc=_clean_desc(str(getattr(pred, "generated_desc", "") or "")),
            generated_summary=_normalize_summary_timestamps(
                str(getattr(pred, "generated_summary", "") or "").strip()
            ),
            debug=debug,
        )

    def _summarize_raw(self, *, lecture_transcript: str) -> dspy.Prediction:
        """Run the `ChainOfThought` call inside the worker thread.

        Binds the LM via `dspy.settings.context` so configuration is set in the
        thread where the DSPy call actually runs (cf. `asyncio.to_thread`).
        """
        with dspy.settings.context(lm=self._get_lm()):
            return self.summarizer(lecture_transcript=lecture_transcript)


# --- Process-level singleton ---


@lru_cache(maxsize=1)
def get_lecture_artifact_generator() -> LectureArtifactGenerator:
    """Return a shared `LectureArtifactGenerator` for this process.

    Stateless after construction, so a single instance is safe to reuse and
    saves rebuilding DSPy modules on every lecture.
    """
    return LectureArtifactGenerator()
