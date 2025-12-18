from __future__ import annotations

from pathlib import Path
from uuid import UUID


def backend_root() -> Path:
    # backend/app/rag/paths.py -> backend/
    return Path(__file__).resolve().parents[2]


def rag_store_root(*, rag_store_dir: str) -> Path:
    base = (rag_store_dir or "").strip()
    if not base:
        base = ".rag_store"
    p = Path(base)
    if not p.is_absolute():
        p = backend_root() / p
    return p


def course_persist_dir(*, rag_store_dir: str, user_id: int, course_id: UUID) -> Path:
    return rag_store_root(rag_store_dir=rag_store_dir) / "users" / str(user_id) / "courses" / str(course_id)


