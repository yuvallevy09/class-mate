"""Integration tests for the SSE streaming endpoint POST /chat-v2/stream (M3).

Authed httpx against the real app + a live Postgres (skips if unreachable). The
TeachingAssistant is replaced via a dependency override with a fake whose
`astream` yields canned events — no LLM. Mirrors the auth/CSRF/DB harness in
test_course_chat.py.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai.stream_events import (
    AnswerEvent,
    CitationsEvent,
    DoneEvent,
    StatusEvent,
    ThinkingEvent,
)
from app.ai.teaching_assistant import get_teaching_assistant
from app.core.security import hash_password
from app.core.settings import get_settings
from app.db.models.user import User
from app.main import app
from app.schemas.retrieval import RetrievedDoc


async def _can_connect(database_url: str) -> bool:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


def _run_migrations_sync() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")


async def _create_user(database_url: str, *, email: str, password: str) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            session.add(User(email=email, hashed_password=hash_password(password), display_name="T"))
            await session.commit()
    finally:
        await engine.dispose()


def _doc() -> RetrievedDoc:
    return RetrievedDoc(text="Eigenvalues are scalars.", lecture_id=uuid4(), lecture_slug="L1", lecture_title="Intro")


def _happy_events() -> list:
    doc = _doc()
    return [
        StatusEvent(stage="searching", label="Searching…"),
        ThinkingEvent(delta="router chose retrieve. "),
        ThinkingEvent(delta="picking L1."),
        CitationsEvent(docs=[doc]),
        StatusEvent(stage="generating", label=None),
        AnswerEvent(delta="Eigenvalues are "),
        AnswerEvent(delta="scalars [1]."),
        DoneEvent(
            answer="Eigenvalues are scalars [1].",
            route="retrieve",
            retrieval_path="hybrid",
            retrieved_docs=[doc],
            debug={},
        ),
    ]


class _FakeTA:
    """astream yields canned events; an Exception in the list is raised when reached."""

    def __init__(self, events: list):
        self._events = events

    async def astream(self, *, db, course_info, conversation_history, user_query):  # noqa: ARG002
        for ev in self._events:
            if isinstance(ev, Exception):
                raise ev
            yield ev


def _parse_frames(body: str) -> list[dict]:
    frames = []
    for block in body.split("\n\n"):
        block = block.strip()
        if block.startswith("data:"):
            frames.append(json.loads(block[len("data:"):].strip()))
    return frames


async def _login_and_make_course(client: httpx.AsyncClient, settings, email: str, password: str) -> tuple[str, str]:
    csrf = await client.get("/api/v1/auth/csrf")
    token = csrf.json()["csrfToken"]
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={settings.csrf_header_name: token},
    )
    assert login.status_code == 200
    course = await client.post(
        "/api/v1/courses",
        json={"name": "Course", "description": None},
        headers={settings.csrf_header_name: token},
    )
    assert course.status_code == 200
    return token, course.json()["id"]


@pytest.mark.asyncio
async def test_stream_happy_path_orders_frames_and_persists(monkeypatch) -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")
    await asyncio.to_thread(_run_migrations_sync)

    email, password = f"stream-{uuid4()}@e.com", "pw"
    await _create_user(settings.database_url, email=email, password=password)

    # New conversation => title generation fires; stub it so no LLM is hit.
    from app.api.v1 import chat_v2 as chat_v2_mod

    async def _no_title(**kwargs):  # noqa: ARG001
        return None

    monkeypatch.setattr(chat_v2_mod, "_maybe_generate_title", _no_title)
    app.dependency_overrides[get_teaching_assistant] = lambda: _FakeTA(_happy_events())

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            token, course_id = await _login_and_make_course(c, settings, email, password)

            resp = await c.post(
                f"/api/v1/courses/{course_id}/chat-v2/stream",
                json={"message": "what are eigenvalues?", "conversationId": None},
                headers={settings.csrf_header_name: token},
            )

            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            frames = _parse_frames(resp.text)
            types = [f["type"] for f in frames]
            # Core contract: citations arrive before any answer delta; done is last.
            assert "citations" in types and "answer" in types
            assert types.index("citations") < types.index("answer")
            assert types[-1] == "done"
            # Thinking still streams live (just isn't persisted — asserted below).
            assert "thinking" in types

            citations_frame = next(f for f in frames if f["type"] == "citations")
            assert len(citations_frame["citations"]) == 1

            done_frame = frames[-1]
            convo_id = done_frame["conversationId"]
            assert convo_id
            assert done_frame["text"]  # final persisted (link-formatted) answer

            # Persistence: user + assistant message stored.
            msgs = await c.get(f"/api/v1/conversations/{convo_id}/messages")
            assert msgs.status_code == 200
            messages = msgs.json()
            assert [m["role"] for m in messages] == ["user", "assistant"]
            assert messages[0]["content"] == "what are eigenvalues?"
            assert messages[1]["content"]  # citation-linked answer
            assert messages[1]["citations"] is not None
            # The done frame hands the client the exact persisted text, so the
            # live→persisted handoff in the UI matches after normalization.
            assert done_frame["text"] == messages[1]["content"]
            # Thinking is live-only: streamed (asserted above) but NOT persisted.
            assert messages[1]["thinking"] is None
    finally:
        app.dependency_overrides.pop(get_teaching_assistant, None)


@pytest.mark.asyncio
async def test_stream_error_path_emits_error_frame_and_persists_fallback(monkeypatch) -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")
    await asyncio.to_thread(_run_migrations_sync)

    email, password = f"stream-err-{uuid4()}@e.com", "pw"
    await _create_user(settings.database_url, email=email, password=password)

    from app.api.v1 import chat_v2 as chat_v2_mod

    async def _no_title(**kwargs):  # noqa: ARG001
        return None

    monkeypatch.setattr(chat_v2_mod, "_maybe_generate_title", _no_title)
    # Stream a couple answer deltas, then blow up mid-stream.
    events = [
        StatusEvent(stage="searching", label="Searching…"),
        AnswerEvent(delta="partial "),
        RuntimeError("LLM exploded"),
    ]
    app.dependency_overrides[get_teaching_assistant] = lambda: _FakeTA(events)

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            token, course_id = await _login_and_make_course(c, settings, email, password)
            resp = await c.post(
                f"/api/v1/courses/{course_id}/chat-v2/stream",
                json={"message": "boom?", "conversationId": None},
                headers={settings.csrf_header_name: token},
            )

            assert resp.status_code == 200  # status locked once streaming starts
            frames = _parse_frames(resp.text)
            assert frames[-1]["type"] == "error"

            # The turn is not left dangling: an assistant message was persisted.
            # Find the conversation via the user's conversation list.
            convos = await c.get(f"/api/v1/courses/{course_id}/conversations")
            assert convos.status_code == 200
            convo_id = convos.json()[0]["id"]
            messages = (await c.get(f"/api/v1/conversations/{convo_id}/messages")).json()
            assert [m["role"] for m in messages] == ["user", "assistant"]
    finally:
        app.dependency_overrides.pop(get_teaching_assistant, None)


@pytest.mark.asyncio
async def test_stream_guards_auth_csrf_ownership() -> None:
    settings = get_settings()
    if not await _can_connect(settings.database_url):
        pytest.skip("Database not reachable. Start Postgres (backend/docker-compose.yml).")
    await asyncio.to_thread(_run_migrations_sync)

    owner_email, other_email, password = (
        f"owner-{uuid4()}@e.com",
        f"other-{uuid4()}@e.com",
        "pw",
    )
    await _create_user(settings.database_url, email=owner_email, password=password)
    await _create_user(settings.database_url, email=other_email, password=password)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        csrf = await c.get("/api/v1/auth/csrf")
        token = csrf.json()["csrfToken"]

        # No auth cookie (but valid CSRF) => 401.
        unauth = await c.post(
            f"/api/v1/courses/{uuid4()}/chat-v2/stream",
            json={"message": "hi", "conversationId": None},
            headers={settings.csrf_header_name: token},
        )
        assert unauth.status_code == 401

        # Owner logs in + creates a course.
        token, course_id = await _login_and_make_course(c, settings, owner_email, password)

        # Missing CSRF header => 403 (middleware).
        missing_csrf = await c.post(
            f"/api/v1/courses/{course_id}/chat-v2/stream",
            json={"message": "hi"},
        )
        assert missing_csrf.status_code == 403

    # Other user can't stream on the owner's course => 404.
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c2:
        token2, _ = await _login_and_make_course(c2, settings, other_email, password)
        forbidden = await c2.post(
            f"/api/v1/courses/{course_id}/chat-v2/stream",
            json={"message": "hi", "conversationId": None},
            headers={settings.csrf_header_name: token2},
        )
        assert forbidden.status_code == 404
