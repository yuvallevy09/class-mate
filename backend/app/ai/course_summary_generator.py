"""DSPy module for generating a course-level AI summary.

Given the course's lectures in chronological order (each with its per-lecture
AI summary), produces a single "what we've learned so far" recap shown on the
Course Overview page and fed into `CourseInfo.summary`.

Mirrors `lecture_artifact_generator.py`:

- DSPy does blocking HTTP I/O; `aforward` wraps the call in `asyncio.to_thread`
  so the FastAPI event loop stays responsive.
- The LM is built lazily from `Settings` and reused via the process-level
  singleton. Tests can inject a pre-built `lm`.
- Lecture summaries carry `[#M:SS]` timestamp citations that are video-relative
  and meaningless at course level; the signature forbids them and
  `_strip_timestamp_brackets` removes any the LM copies in anyway.
"""

from __future__ import annotations

import asyncio
import re
from functools import lru_cache
from typing import Any

import dspy
from pydantic import BaseModel, Field

from app.ai.llm import build_lm
from app.core.settings import Settings, get_settings


# Output is a few short paragraphs; ChainOfThought's `reasoning` shares this
# budget, so leave headroom above the expected prose length.
_DEFAULT_MAX_TOKENS = 4_096


# --- Signature ---


class SummarizeCourse(dspy.Signature):
    """You are a teaching assistant writing a running recap of a course for its students. Given the course's lectures in chronological order, each with its own summary, write a "what we've learned so far" summary of the whole course."""

    course_context: str = dspy.InputField(
        desc=(
            "Course name and description, followed by each lecture's title and "
            "summary in chronological order."
        )
    )

    generated_course_summary: str = dspy.OutputField(
        desc=(
            "2-4 short paragraphs of plain prose (no headings or bullet lists) "
            "summarizing what the course has covered so far, narrating the arc "
            "from the first lecture to the most recent.\n"
            "- Be faithful to the lecture summaries only. Do not invent content, "
            "and do not pad with filler.\n"
            "- Do NOT include timestamps such as '[#12:34]'; they refer to "
            "positions inside individual videos and are meaningless here."
        )
    )


# --- Result ---


class CourseSummary(BaseModel):
    """Structured, post-processed course summary.

    `debug` carries non-user-facing diagnostics (the `ChainOfThought` reasoning
    string).
    """

    generated_summary: str
    debug: dict[str, Any] = Field(default_factory=dict)


# --- Post-processing ---


# `[#M:SS]` or comma-separated lists like `[#0:04, #6:06]`, with the leading
# whitespace so removal doesn't leave double spaces.
_TIMESTAMP_BRACKET_RE = re.compile(r"\s*\[(?:\s*#?\d{1,3}:\d{2}\s*,?)+\]")


def _strip_timestamp_brackets(text: str) -> str:
    """Remove lecture-level `[#M:SS]` citation brackets if the LM copies them in."""
    if not text:
        return text
    out = _TIMESTAMP_BRACKET_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


# --- Module ---


class CourseSummaryGenerator(dspy.Module):
    """DSPy orchestrator for the course-level summary.

    Stateless after construction; safe to share across requests via the
    process-level singleton. Pass a pre-built `lm` (e.g. a DSPy `DummyLM`) to
    short-circuit LM construction in tests.
    """

    def __init__(self, *, lm: dspy.LM | None = None) -> None:
        super().__init__()
        self._lm: dspy.LM | None = lm
        self.summarizer = dspy.ChainOfThought(SummarizeCourse)

    def _get_lm(self) -> dspy.LM:
        if self._lm is None:
            settings: Settings = get_settings()
            self._lm = build_lm(
                settings,
                temperature=float(settings.chat_temperature),
                max_tokens=_DEFAULT_MAX_TOKENS,
            )
        return self._lm

    async def aforward(self, *, course_context: str) -> CourseSummary:
        ctx = (course_context or "").strip()
        if not ctx:
            raise ValueError("course_context must be non-empty")

        pred = await asyncio.to_thread(self._summarize_raw, course_context=ctx)

        debug: dict[str, Any] = {}
        reasoning = str(getattr(pred, "reasoning", "") or "").strip()
        if reasoning:
            debug["reasoning"] = reasoning

        return CourseSummary(
            generated_summary=_strip_timestamp_brackets(
                str(getattr(pred, "generated_course_summary", "") or "").strip()
            ),
            debug=debug,
        )

    def _summarize_raw(self, *, course_context: str) -> dspy.Prediction:
        """Run the `ChainOfThought` call inside the worker thread.

        Binds the LM via `dspy.settings.context` so configuration is set in the
        thread where the DSPy call actually runs (cf. `asyncio.to_thread`).
        """
        with dspy.settings.context(lm=self._get_lm()):
            return self.summarizer(course_context=course_context)


# --- Process-level singleton ---


@lru_cache(maxsize=1)
def get_course_summary_generator() -> CourseSummaryGenerator:
    """Return a shared `CourseSummaryGenerator` for this process."""
    return CourseSummaryGenerator()
