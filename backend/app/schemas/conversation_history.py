from __future__ import annotations

from typing import Iterable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Pedagogical role exposed to the LLM.
StudentTeacherRole = Literal["student", "teacher"]

# Map from the canonical chat-message role used in storage (and across the
# rest of the API) to the pedagogical label we expose to the TeachingAssistant
# prompts. The DB schema stays unchanged — translation happens at construction.
_DB_ROLE_TO_PROMPT_ROLE: dict[str, StudentTeacherRole] = {
    "user": "student",
    "assistant": "teacher",
}


class _MessageLike(Protocol):
    role: str
    content: str


class ConversationTurn(BaseModel):
    """A single utterance in a chat.

    `role` is the pedagogical label ('student' or 'teacher'). Translation from
    the storage role ('user' / 'assistant') happens in
    `ConversationHistory.from_chat_messages`.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    role: StudentTeacherRole
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def _strip_content(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("ConversationTurn.content cannot be empty")
        return s


class ConversationHistory(BaseModel):
    """The last N messages between the student and the teaching assistant.

    Built once per chat turn (before persisting the current user message) and
    passed to every DSPy signature so each sub-call sees the same view of the
    dialog. The current user message is NOT part of `turns` — it is passed
    separately as `user_query` to the TeachingAssistant.
    """

    model_config = ConfigDict(from_attributes=True)

    turns: list[ConversationTurn] = Field(default_factory=list)

    @classmethod
    def from_chat_messages(
        cls,
        messages: Iterable[_MessageLike],
        *,
        max_n: int | None = None,
    ) -> "ConversationHistory":
        """Build from `ChatMessage` ORM rows or any object exposing `role`+`content`.

        - Translates roles via `_DB_ROLE_TO_PROMPT_ROLE`.
        - Skips entries with empty/whitespace-only content.
        - Skips entries with unrecognized roles (defensive — we'd rather drop
          one row than refuse to chat).
        - When `max_n` is set, keeps only the last `max_n` turns (after
          filtering). Input ordering is preserved otherwise.
        """
        out: list[ConversationTurn] = []
        for m in messages:
            db_role = (getattr(m, "role", None) or "").strip().lower()
            prompt_role = _DB_ROLE_TO_PROMPT_ROLE.get(db_role)
            if prompt_role is None:
                continue
            content = (getattr(m, "content", "") or "").strip()
            if not content:
                continue
            out.append(ConversationTurn(role=prompt_role, content=content))
        if max_n is not None and max_n >= 0:
            # Note: `out[-0:]` is `out[0:]` (the full list), not empty — guard explicitly.
            out = out[-max_n:] if max_n > 0 else []
        return cls(turns=out)

    def cap(self, max_n: int) -> "ConversationHistory":
        """Return a new history capped to the last `max_n` turns."""
        if max_n <= 0:
            return ConversationHistory(turns=[])
        return ConversationHistory(turns=list(self.turns[-max_n:]))

    def is_empty(self) -> bool:
        return not self.turns

    @property
    def last_student_message(self) -> str | None:
        for t in reversed(self.turns):
            if t.role == "student":
                return t.content
        return None

    def to_prompt_string(self) -> str:
        """Render as labelled 'Student:' / 'Teacher:' lines for LLM consumption.

        Returns a sentinel string when empty so DSPy signatures don't receive
        an empty input field (which some adapters render awkwardly).
        """
        if not self.turns:
            return "(no prior messages)"
        lines: list[str] = []
        for t in self.turns:
            label = "Student" if t.role == "student" else "Teacher"
            lines.append(f"{label}: {t.content}")
        return "\n".join(lines)
