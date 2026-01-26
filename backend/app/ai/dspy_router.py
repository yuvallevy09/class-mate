from __future__ import annotations

import json
from dataclasses import dataclass

import dspy
from json_repair import repair_json

from app.core.settings import Settings


@dataclass(frozen=True)
class RouteDecision:
    route: str  # "course_specific" | "general" | "mixed" | "web_required"
    needs_course_retrieval: bool
    needs_web: bool
    clarifying_questions: list[str]
    retrieval_hints: dict


class _RouteSig(dspy.Signature):
    """
    Classify the user query into one of:
    - course_specific: requires course materials to answer well
    - general: can be answered from general knowledge
    - mixed: ambiguous or both; course context may help
    - web_required: requires up-to-date web knowledge (recent events, changing info)

    Output a single JSON object string with keys:
    {
      "route": "course_specific|general|mixed|web_required",
      "needs_course_retrieval": true|false,
      "needs_web": true|false,
      "clarifying_questions": ["...", "..."],   // up to 2
      "retrieval_hints": {                      // optional
        "doc_types": ["pdf","slides","segment"],
        "categories": ["notes","exams","homework"],
        "lecture_ref": "Lecture 3",
        "time_ref": "27:36",
        "assignment_ref": "HW2",
        "exam_ref": "Midterm"
      }
    }
    """

    course_name: str = dspy.InputField()
    course_description: str = dspy.InputField()
    history_summary: str = dspy.InputField(desc="Short summary of recent chat context")
    user_message: str = dspy.InputField()

    decision_json: str = dspy.OutputField(desc="JSON object as specified in the instructions")


class GeminiRouter(dspy.Module):
    def __init__(self) -> None:
        super().__init__()
        self._predict = dspy.Predict(_RouteSig)

    def forward(
        self,
        *,
        course_name: str,
        course_description: str,
        history_summary: str,
        user_message: str,
    ) -> str:
        return self._predict(
            course_name=course_name,
            course_description=course_description,
            history_summary=history_summary,
            user_message=user_message,
        ).decision_json


def _default_decision() -> RouteDecision:
    # Conservative default: retrieve course content so we don't miss citations.
    return RouteDecision(
        route="mixed",
        needs_course_retrieval=True,
        needs_web=False,
        clarifying_questions=[],
        retrieval_hints={},
    )


def route_message(*, settings: Settings, course_name: str, course_description: str | None, history: str, message: str) -> RouteDecision:
    """
    Best-effort router. Never raises: on failure, falls back to a conservative 'mixed' decision.
    """
    model = (getattr(settings, "dspy_router_model", None) or "gemini/gemini-2.0-flash").strip()
    try:
        lm = dspy.LM(model=model, temperature=0.0, max_tokens=350, num_retries=1)
        router = GeminiRouter()
        with dspy.settings.context(lm=lm):
            raw = router(
                course_name=str(course_name or "").strip(),
                course_description=str(course_description or "").strip(),
                history_summary=str(history or "").strip(),
                user_message=str(message or "").strip(),
            )

        txt = (raw or "").strip()
        if not txt:
            return _default_decision()

        repaired = repair_json(txt)
        data = json.loads(repaired)

        route = str(data.get("route") or "").strip().lower()
        if route not in {"course_specific", "general", "mixed", "web_required"}:
            route = "mixed"

        needs_course_retrieval = bool(data.get("needs_course_retrieval")) if "needs_course_retrieval" in data else (route != "general")
        needs_web = bool(data.get("needs_web")) if "needs_web" in data else (route == "web_required")

        cqs = data.get("clarifying_questions") or []
        if not isinstance(cqs, list):
            cqs = []
        clarifying_questions = [str(x).strip() for x in cqs if str(x).strip()][:2]

        hints = data.get("retrieval_hints") or {}
        if not isinstance(hints, dict):
            hints = {}

        return RouteDecision(
            route=route,
            needs_course_retrieval=bool(needs_course_retrieval),
            needs_web=bool(needs_web),
            clarifying_questions=clarifying_questions,
            retrieval_hints=hints,
        )
    except Exception:
        return _default_decision()


