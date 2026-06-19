from __future__ import annotations

from app.core.settings import Settings
from app.ai.dspy_chat import enforce_title_constraints, generate_title_dspy


class ChatEngine:
    """
    Conversation-title helper.

    Originally this also generated chat replies (the v1 `course_chat` path), but
    that has been superseded by `TeachingAssistant` (v2). All that remains is the
    best-effort title generation reused by the v2 endpoint.
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    async def generate_title(self, *, course_name: str, first_user_message: str) -> str | None:
        """
        Generate a short 3–5 word title for a new conversation based on the first user message.

        Best-effort: returns None if a title can't be generated.
        """
        text = ""
        try:
            text = generate_title_dspy(
                settings=self._settings, course_name=course_name, first_user_message=first_user_message
            )
        except Exception:
            # Fall back to a deterministic title derived from the first message.
            text = ""
        return enforce_title_constraints(text, fallback_message=first_user_message)
