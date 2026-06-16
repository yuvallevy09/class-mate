"""Per-task model selection for the DSPy pipeline.

`llm.py` builds a single global LM from `Settings.llm_provider`. This module
layers a **role -> tier -> (model, api key)** mapping on top of it so each
pipeline step can use a model sized to its job: cheap/fast models for
high-volume internal steps (routing, titles), the flagship only for the
correctness-sensitive steps (retrieval query generation, cited answers).

Safety by construction: `build_lm_for_role` falls back to the global
`build_lm()` whenever per-task selection is disabled (`MODEL_ROLES_ENABLED`)
or the resolved tier's provider key is missing. So dev (which typically has
only `GOOGLE_API_KEY`) keeps working unchanged, and a missing key degrades to
the global provider rather than erroring.
"""

from __future__ import annotations

import dspy

from app.ai.llm import build_lm
from app.core.settings import Settings

# --- Roles (one per LLM call site we tier) ---

ROUTER = "router"
CLARIFY = "clarify"
ANSWER_NO_CTX = "answer_no_ctx"
GEN_RETRIEVAL = "gen_retrieval_params"
ANSWER_WITH_CTX = "answer_with_ctx"
TITLE = "title"
CHAPTERS = "chapters"
COURSE_SUMMARY = "course_summary"
LECTURE_SUMMARY = "lecture_summary"

# --- Tier assignment per role (the mapping decision lives here) ---
#
# flash  = optional cheapest tier (Gemini). Currently unused — router/title were
#          moved to haiku to keep the pipeline on a single provider (Anthropic)
#          and avoid Gemini billing. Re-point a role here to use Flash again.
# haiku  = high-volume internal steps (router/title) + user-facing prose without
#          citations + background structured work
# sonnet = the two correctness gates (retrieval-in, answer-out) + the
#          per-lecture summary that feeds retrieval context
#
# To re-tier a step (e.g. move gen_retrieval_params to haiku once a retrieval
# eval confirms quality holds), change a single entry below.
ROLE_TIERS: dict[str, str] = {
    ROUTER: "haiku",
    CLARIFY: "haiku",
    ANSWER_NO_CTX: "haiku",
    GEN_RETRIEVAL: "sonnet",
    ANSWER_WITH_CTX: "sonnet",
    TITLE: "haiku",
    CHAPTERS: "haiku",
    COURSE_SUMMARY: "haiku",
    LECTURE_SUMMARY: "sonnet",
}


def _with_prefix(model: str, prefix: str) -> str:
    """LiteLLM model id: pass through if already provider-qualified."""
    m = (model or "").strip()
    return m if "/" in m else f"{prefix}/{m}"


def _resolve_tier(settings: Settings, tier: str) -> tuple[str, str | None]:
    """Return (litellm_model_id, api_key) for a tier."""
    if tier == "flash":
        return _with_prefix(settings.gemini_model, "gemini"), settings.google_api_key
    if tier == "haiku":
        return _with_prefix(settings.anthropic_haiku_model, "anthropic"), settings.anthropic_api_key
    if tier == "sonnet":
        return _with_prefix(settings.anthropic_model, "anthropic"), settings.anthropic_api_key
    raise ValueError(f"Unknown model tier: {tier!r}")


def build_lm_for_role(
    settings: Settings,
    role: str,
    *,
    max_tokens: int,
    temperature: float = 0.0,
    num_retries: int = 2,
) -> dspy.LM:
    """Build a DSPy LM for a pipeline role.

    Falls back to the global `build_lm()` when per-task selection is disabled
    or the resolved tier's provider key is missing — so this never makes a
    request worse-configured than the single-provider baseline.
    """
    if not getattr(settings, "model_roles_enabled", True):
        return build_lm(
            settings, temperature=temperature, max_tokens=max_tokens, num_retries=num_retries
        )

    tier = ROLE_TIERS.get(role)
    if tier is None:
        raise ValueError(f"Unknown model role: {role!r}")

    model_id, api_key = _resolve_tier(settings, tier)
    if not (api_key or "").strip():
        # Tier's provider isn't configured (e.g. dev with only GOOGLE_API_KEY):
        # degrade to the global provider rather than failing.
        return build_lm(
            settings, temperature=temperature, max_tokens=max_tokens, num_retries=num_retries
        )

    return dspy.LM(
        model=model_id,
        api_key=api_key,
        temperature=float(temperature),
        max_tokens=max_tokens,
        num_retries=num_retries,
    )


def resolved_model_id(settings: Settings, role: str) -> str:
    """The LiteLLM model id a role will actually use (for debug/observability).

    Mirrors `build_lm_for_role`'s fallback logic so debug output reflects what
    really ran.
    """
    if not getattr(settings, "model_roles_enabled", True):
        from app.ai.llm import litellm_model_id

        return litellm_model_id(settings)
    tier = ROLE_TIERS.get(role)
    if tier is None:
        raise ValueError(f"Unknown model role: {role!r}")
    model_id, api_key = _resolve_tier(settings, tier)
    if not (api_key or "").strip():
        from app.ai.llm import litellm_model_id

        return litellm_model_id(settings)
    return model_id
