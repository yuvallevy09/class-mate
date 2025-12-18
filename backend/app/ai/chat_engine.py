from __future__ import annotations

from dataclasses import dataclass
import re

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

    def _build_title_prompt(self, *, course_name: str, first_user_message: str) -> list:
        """
        Build a prompt that returns ONLY a 3–5 word title, no quotes/punctuation.
        """
        system = (
            "You generate short chat titles.\n"
            "Return ONLY a concise 3–5 word title.\n"
            "Rules:\n"
            "- 3–5 words exactly\n"
            "- No surrounding quotes\n"
            "- No punctuation at the end\n"
            "- No emojis\n"
            "- Title-case is OK but not required\n"
        )
        user = (
            f"Course: {course_name}\n"
            f"First message: {first_user_message.strip()}\n"
            "Title:"
        )
        return [SystemMessage(content=system), HumanMessage(content=user)]

    def _enforce_title_constraints(self, title: str, *, fallback_message: str) -> str | None:
        """
        Normalize and enforce a 3–5 word title. Returns None if it can't produce
        something reasonable.
        """
        raw = (title or "").strip()
        if not raw:
            raw = ""

        # Remove surrounding quotes/backticks and collapse whitespace.
        raw = raw.strip().strip('"\''"`“”‘’")
        raw = re.sub(r"\s+", " ", raw).strip()

        # Drop trailing punctuation like ":" "." "!" etc.
        raw = re.sub(r"[.?!:;,\-–—]+$", "", raw).strip()

        # Keep only word-like tokens (letters/numbers). This intentionally drops punctuation.
        def words(s: str) -> list[str]:
            return re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", s)

        w = words(raw)

        # If model output isn't usable, fallback to first user message keywords.
        if len(w) < 3:
            fw = words((fallback_message or "").strip())
            if not fw:
                return None
            w = fw

        # Enforce 3–5 words.
        w = w[:5]
        if len(w) < 3:
            return None

        # Reconstruct title with spaces, cap length defensively.
        final = " ".join(w).strip()
        if not final:
            return None
        return final[:80]

    async def generate_title(self, *, course_name: str, first_user_message: str) -> str | None:
        """
        Generate a short 3–5 word title for a new conversation based on the first user message.

        Best-effort: returns None if a title can't be generated.
        """
        llm = ChatGoogleGenerativeAI(
            model=self._settings.gemini_model,
            api_key=self._effective_api_key(),
            temperature=0.0,
        )

        messages = self._build_title_prompt(course_name=course_name, first_user_message=first_user_message)
        res = await llm.ainvoke(messages)
        text = (getattr(res, "content", "") or "").strip()
        return self._enforce_title_constraints(text, fallback_message=first_user_message)

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


