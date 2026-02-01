from __future__ import annotations

import re

import dspy

from app.core.settings import Settings


def _litellm_gemini_model(model: str) -> str:
    m = (model or "").strip()
    if not m:
        m = "gemini-2.5-flash"
    # DSPy uses LiteLLM model identifiers. Gemini is typically "gemini/<model>".
    if "/" in m:
        return m
    return f"gemini/{m}"


def _effective_gemini_api_key(settings: Settings) -> str:
    key = (settings.google_api_key or "").strip()
    if not key:
        raise ValueError("Missing Gemini API key. Set GOOGLE_API_KEY in backend/.env.")
    return key


class _ReplySig(dspy.Signature):
    """Answer the user's question. If provided, use course excerpts and cite them as [#]."""

    system_prompt: str = dspy.InputField()
    history: str = dspy.InputField(desc="Conversation history, most recent last")
    rag_context: str = dspy.InputField(desc="Retrieved course excerpts with [#] labels. May be empty.")
    user_message: str = dspy.InputField()

    answer: str = dspy.OutputField(
        desc=(
            "Helpful answer. If you use retrieved excerpts, cite them by [#]. "
            "If excerpts are insufficient, say so and ask a clarifying question."
        )
    )


class _TitleSig(dspy.Signature):
    """Return ONLY a concise 3–5 word title, no quotes/punctuation."""

    course_name: str = dspy.InputField()
    first_user_message: str = dspy.InputField()
    title: str = dspy.OutputField()


def generate_reply_dspy(
    *,
    settings: Settings,
    system_prompt: str,
    history: str,
    rag_context: str,
    user_message: str,
) -> str:
    # Configure LM per-call (important in async servers).
    _effective_gemini_api_key(settings)  # validate early (fails fast, no network)
    lm = dspy.LM(
        model=_litellm_gemini_model(settings.gemini_model),
        temperature=float(settings.chat_temperature),
        max_tokens=900,
        num_retries=2,
    )
    pred = dspy.Predict(_ReplySig)
    with dspy.settings.context(lm=lm):
        out = pred(
            system_prompt=(system_prompt or "").strip(),
            history=(history or "").strip(),
            rag_context=(rag_context or "").strip(),
            user_message=(user_message or "").strip(),
        )
    return (getattr(out, "answer", "") or "").strip()


def generate_title_dspy(*, settings: Settings, course_name: str, first_user_message: str) -> str:
    _effective_gemini_api_key(settings)  # validate early
    lm = dspy.LM(model=_litellm_gemini_model(settings.gemini_model), temperature=0.0, max_tokens=40, num_retries=2)
    pred = dspy.Predict(_TitleSig)
    with dspy.settings.context(lm=lm):
        out = pred(course_name=(course_name or "").strip(), first_user_message=(first_user_message or "").strip())
    return (getattr(out, "title", "") or "").strip()


def enforce_title_constraints(title: str, *, fallback_message: str) -> str | None:
    """
    Normalize and enforce a 3–5 word title. Returns None if it can't produce something reasonable.
    """
    raw = (title or "").strip()
    if not raw:
        raw = ""

    raw = raw.strip().strip('"\''"`“”‘’")
    raw = re.sub(r"\s+", " ", raw).strip()
    raw = re.sub(r"[.?!:;,\-–—]+$", "", raw).strip()

    def words(s: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", s)

    w = words(raw)
    if len(w) < 3:
        fw = words((fallback_message or "").strip())
        if not fw:
            return None
        w = fw

    w = w[:5]
    if len(w) < 3:
        return None

    final = " ".join(w).strip()
    if not final:
        return None
    return final[:80]


