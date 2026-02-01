from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.settings import Settings
from app.ai.dspy_chat import enforce_title_constraints, generate_reply_dspy, generate_title_dspy
from app.rag.types import RagHit
from app.schemas.chat import ChatCitation


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

    def _build_rag_context_prompt(self, hits) -> str:
        """
        Provide retrieved excerpts as a separate system message.
        Keep it short to avoid ballooning prompt size.
        """
        lines: list[str] = [
            "Course materials excerpts (retrieved):",
            "Use these excerpts to answer if relevant. If they are insufficient, say so.",
            "Do not invent citations. If you use an excerpt, cite it by its [#] number.",
            "",
        ]

        for i, hit in enumerate(hits, start=1):
            snippet = (hit.text or "").strip()
            if len(snippet) > 900:
                snippet = snippet[:900].rstrip() + "…"
            lines.append(f"[{i}] {snippet}")
            meta = hit.metadata or {}
            src = []
            if meta.get("original_filename"):
                src.append(str(meta["original_filename"]))
            # PDF/PPTX chunk citations (Postgres retrieval layer uses page_start/page_end).
            if meta.get("page_start") or meta.get("page_end"):
                try:
                    ps = int(meta.get("page_start") or meta.get("page_end") or 0)
                    pe = int(meta.get("page_end") or meta.get("page_start") or ps)
                    if ps and pe:
                        if meta.get("source_kind") == "pptx" and meta.get("slide_no"):
                            # Prefer slide citation for PPTX (but keep page range too).
                            src.append(f"slide.{int(meta.get('slide_no'))}")
                        src.append(f"p.{ps}" if ps == pe else f"p.{ps}-{pe}")
                except Exception:
                    pass
            if meta.get("video_asset_id"):
                # Video transcript segment (video asset)
                try:
                    start = float(meta.get("start_sec") or 0.0)
                    end = float(meta.get("end_sec") or 0.0)
                    src.append(f"video_asset:{meta['video_asset_id']} {start:.0f}s→{end:.0f}s")
                except Exception:
                    src.append(f"video_asset:{meta['video_asset_id']}")
            if meta.get("chapter_title"):
                src.append(f"chapter:{meta['chapter_title']}")
            if src:
                lines.append(f"    Source: {' '.join(src)}")
        return "\n".join(lines).strip() + "\n"

    def _hits_to_citations(self, hits) -> list[ChatCitation]:
        citations: list[ChatCitation] = []
        for hit in hits:
            meta = hit.metadata or {}
            content_id = meta.get("content_id")
            try:
                content_uuid = UUID(str(content_id)) if content_id else None
            except ValueError:
                content_uuid = None
            # Video metadata (best-effort)
            extra = {
                "page": meta.get("page"),
                "pageStart": meta.get("page_start"),
                "pageEnd": meta.get("page_end"),
                "sourceKind": meta.get("source_kind"),
                "slideNo": meta.get("slide_no"),
                "original_filename": meta.get("original_filename"),
                "score": hit.score,
                "doc_type": meta.get("doc_type"),
            }
            if meta.get("video_asset_id"):
                extra.update(
                    {
                        "type": "video",
                        "videoAssetId": meta.get("video_asset_id"),
                        "startSec": meta.get("start_sec"),
                        "endSec": meta.get("end_sec"),
                        "languageCode": meta.get("language_code"),
                        "chapterTitle": meta.get("chapter_title"),
                    }
                )
            citations.append(
                ChatCitation(
                    content_id=content_uuid,
                    title=meta.get("title") or meta.get("chapter_title"),
                    url=None,
                    snippet=(hit.text or "")[:900],
                    extra=extra,
                )
            )
        return citations

    async def generate_title(self, *, course_name: str, first_user_message: str) -> str | None:
        """
        Generate a short 3–5 word title for a new conversation based on the first user message.

        Best-effort: returns None if a title can't be generated.
        """
        text = generate_title_dspy(
            settings=self._settings, course_name=course_name, first_user_message=first_user_message
        )
        return enforce_title_constraints(text, fallback_message=first_user_message)

    async def generate_reply(
        self,
        *,
        user_id: int | None = None,
        course_id: UUID | None = None,
        course_name: str,
        course_description: str | None,
        history: list[ChatHistoryItem],
        user_message: str,
        rag_hits: list[RagHit] | None = None,
    ) -> tuple[str, list[ChatCitation]]:
        # Hard cap: enforce the last N history items at the engine boundary.
        max_n = int(self._settings.chat_history_max_messages)
        history = history[-max_n:] if max_n > 0 else []

        system_prompt = self._build_system_prompt(course_name=course_name, course_description=course_description)
        history_text = "\n".join([f"{h.role}: {h.content}" for h in history]).strip()

        citations: list[ChatCitation] = []

        # RAG (best-effort): caller provides hits from the Postgres retrieval layer.
        hits: list[RagHit] = []
        if rag_hits is not None:
            hits = rag_hits

        if hits:
            rag_prompt = self._build_rag_context_prompt(hits)
            citations = self._hits_to_citations(hits)

        text = generate_reply_dspy(
            settings=self._settings,
            system_prompt=system_prompt,
            history=history_text,
            rag_context=(rag_prompt if hits else ""),
            user_message=user_message,
        )
        return (text or "I’m not sure—could you rephrase your question or provide more course context?"), citations


