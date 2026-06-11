"""Provider-agnostic LLM configuration for DSPy.

Single source of truth for which chat model the app talks to. The provider is
selected by `Settings.llm_provider`:

- "gemini" (dev default): `gemini_model` via GOOGLE_API_KEY
- "anthropic" (prod): `anthropic_model` via ANTHROPIC_API_KEY

DSPy uses LiteLLM-style model identifiers ("<provider>/<model>"); LiteLLM
handles the per-provider API differences, so signatures and modules don't
change when the provider flips.
"""

from __future__ import annotations

import dspy

from app.core.settings import Settings


def _provider(settings: Settings) -> str:
    return (getattr(settings, "llm_provider", "gemini") or "gemini").strip().lower()


def litellm_model_id(settings: Settings) -> str:
    """LiteLLM model identifier for the active provider."""
    if _provider(settings) == "anthropic":
        m = (settings.anthropic_model or "").strip() or "claude-sonnet-4-6"
        return m if "/" in m else f"anthropic/{m}"
    m = (settings.gemini_model or "").strip() or "gemini-2.5-flash"
    return m if "/" in m else f"gemini/{m}"


def effective_llm_api_key(settings: Settings) -> str:
    """Return the API key for the active provider; raise early if missing."""
    if _provider(settings) == "anthropic":
        key = (settings.anthropic_api_key or "").strip()
        if not key:
            raise ValueError("Missing Anthropic API key. Set ANTHROPIC_API_KEY in backend/.env.")
        return key
    key = (settings.google_api_key or "").strip()
    if not key:
        raise ValueError("Missing Gemini API key. Set GOOGLE_API_KEY in backend/.env.")
    return key


def build_lm(
    settings: Settings,
    *,
    max_tokens: int,
    temperature: float = 0.0,
    num_retries: int = 2,
) -> dspy.LM:
    """Build a DSPy LM for the configured provider.

    The API key is passed explicitly so requests don't depend on the key being
    exported in the process environment.
    """
    return dspy.LM(
        model=litellm_model_id(settings),
        api_key=effective_llm_api_key(settings),
        temperature=float(temperature),
        max_tokens=max_tokens,
        num_retries=num_retries,
    )
