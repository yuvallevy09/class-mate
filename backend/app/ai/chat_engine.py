from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.settings import Settings


@dataclass(frozen=True)
class ChatHistoryItem:
    role: str  # "user" | "assistant"
    content: str


class ChatEngine:
    """
    Minimal Phase-1 chat engine:
    - No RAG
    - Uses course name/description + last N messages
    - Explicitly selects api_key and passes it to the model (no implicit env fallback)
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    def _effective_api_key(self) -> str:
        # Prefer a single configured key. If both are set, GOOGLE_API_KEY wins.
        key = (self._settings.google_api_key or "").strip() or (self._settings.gemini_api_key or "").strip()
        if not key:
            raise ValueError(
                "Missing Gemini API key. Set GOOGLE_API_KEY (preferred) or GEMINI_API_KEY in backend/.env."
            )
        return key

    def _build_system_prompt(self, *, course_name: str, course_description: str | None) -> str:
        desc = (course_description or "").strip()
        return (
            "You are ClassMate, a helpful teaching assistant for a single course.\n"
            "Rules:\n"
            "- Stay scoped to the course context provided.\n"
            "- If you lack course materials to answer confidently, ask a clarifying question or suggest what to upload.\n"
            "- Do not fabricate citations. If you reference course materials, describe what you'd need to cite.\n"
            "\n"
            f"Course name: {course_name}\n"
            f"Course description: {desc if desc else '(none)'}\n"
        )

    def _to_lc_messages(self, *, system_prompt: str, history: list[ChatHistoryItem], user_message: str):
        msgs = [SystemMessage(content=system_prompt)]
        for item in history:
            role = (item.role or "").strip().lower()
            if role == "user":
                msgs.append(HumanMessage(content=item.content))
            elif role == "assistant":
                msgs.append(AIMessage(content=item.content))
            # Ignore unknown roles in Phase 1.
        msgs.append(HumanMessage(content=user_message))
        return msgs

    async def generate_reply(
        self,
        *,
        course_name: str,
        course_description: str | None,
        history: list[ChatHistoryItem],
        user_message: str,
    ) -> str:
        # Hard cap: enforce the last N history items at the engine boundary.
        max_n = int(self._settings.chat_history_max_messages)
        history = history[-max_n:] if max_n > 0 else []

        llm = ChatGoogleGenerativeAI(
            model=self._settings.gemini_model,
            api_key=self._effective_api_key(),
            temperature=float(self._settings.chat_temperature),
        )

        system_prompt = self._build_system_prompt(course_name=course_name, course_description=course_description)
        messages = self._to_lc_messages(system_prompt=system_prompt, history=history, user_message=user_message)

        res = await llm.ainvoke(messages)
        text = (getattr(res, "content", "") or "").strip()
        return text or "I’m not sure—could you rephrase your question or provide more course context?"


