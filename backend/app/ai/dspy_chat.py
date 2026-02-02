from __future__ import annotations

import re

import dspy

from app.core.settings import Settings


_TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "can",
    "could",
    "do",
    "does",
    "explain",
    "for",
    "from",
    "help",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "please",
    "tell",
    "the",
    "to",
    "understand",
    "vs",  # keep as a token but we prefer lowercase formatting later
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "you",
    "your",
}


def _tokenize_words(s: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", (s or ""))


def _derive_title_from_message(message: str) -> str | None:
    """
    Build a reasonable 3–5 word title from a user message without calling an LLM.

    Strategy:
    - Strip common question preambles ("can you explain...", etc.)
    - Remove stopwords
    - Keep informative tokens + "vs"
    - Title-case (keep 'vs' lowercase)
    """
    m = (message or "").strip()
    if not m:
        return None

    # Normalize whitespace + remove trailing punctuation.
    m = re.sub(r"\s+", " ", m).strip()
    m = re.sub(r"[.?!:;,\-–—]+$", "", m).strip()

    # Strip very common preambles so we don't get titles like "Can You Explain".
    preambles = [
        r"^(can|could|would)\s+you\s+(please\s+)?",
        r"^please\s+",
        r"^(help\s+me\s+)?understand\s+",
        r"^tell\s+me\s+about\s+",
        r"^what\s+is\s+",
        r"^what\s+are\s+",
        r"^how\s+do\s+i\s+",
        r"^how\s+to\s+",
        r"^explain\s+",
        r"^difference\s+between\s+",
        r"^compare\s+",
    ]
    lowered = m.lower()
    for pat in preambles:
        lowered2 = re.sub(pat, "", lowered, flags=re.IGNORECASE).strip()
        if lowered2 != lowered:
            lowered = lowered2
            break

    # Tokenize from the *original* message for casing hints, but filter using lowercase checks.
    tokens = _tokenize_words(lowered)
    if not tokens:
        return None

    kept: list[str] = []
    for t in tokens:
        tl = t.lower()
        if tl in _TITLE_STOPWORDS:
            # keep "vs" explicitly
            if tl == "vs":
                kept.append("vs")
            continue
        kept.append(tl)

    # If we filtered too aggressively, fall back to unfiltered tokens (still strip preamble).
    base = kept if len(kept) >= 3 else tokens
    base = base[:5]

    # Ensure minimum words; if still too short, use original message tokens (no stopword filter).
    if len(base) < 3:
        raw_tokens = _tokenize_words(m)
        if not raw_tokens:
            return None
        base = [t.lower() for t in raw_tokens][:5]
        if len(base) < 3:
            return None

    # Title-case with a few conventions.
    out_parts: list[str] = []
    for t in base:
        if t.lower() == "vs":
            out_parts.append("vs")
            continue
        # Preserve common acronyms
        if t.upper() in {"API", "SQL", "AWS", "GCP", "LLM", "RAG", "CPU", "GPU"}:
            out_parts.append(t.upper())
        else:
            out_parts.append(t[:1].upper() + t[1:])

    final = " ".join(out_parts).strip()
    return final[:80] if final else None


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

    w = _tokenize_words(raw)
    if len(w) >= 3:
        final = " ".join(w[:5]).strip()
        return final[:80] if final else None

    # Title model output was empty/too short; derive a better title from the user's message.
    derived = _derive_title_from_message(fallback_message)
    if derived:
        return derived[:80]

    # Last resort: stable generic title (still 3 words to satisfy UI expectations).
    return "New Course Chat"


