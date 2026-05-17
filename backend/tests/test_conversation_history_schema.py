from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.schemas.conversation_history import ConversationHistory, ConversationTurn


@dataclass
class _FakeMsg:
    role: str
    content: str


def test_from_chat_messages_translates_roles() -> None:
    history = ConversationHistory.from_chat_messages(
        [
            _FakeMsg(role="user", content="hi"),
            _FakeMsg(role="assistant", content="hello!"),
        ]
    )
    assert [(t.role, t.content) for t in history.turns] == [
        ("student", "hi"),
        ("teacher", "hello!"),
    ]


def test_from_chat_messages_skips_empty_and_whitespace_content() -> None:
    history = ConversationHistory.from_chat_messages(
        [
            _FakeMsg(role="user", content="real"),
            _FakeMsg(role="assistant", content=""),
            _FakeMsg(role="user", content="   "),
            _FakeMsg(role="assistant", content="ok"),
        ]
    )
    assert [(t.role, t.content) for t in history.turns] == [
        ("student", "real"),
        ("teacher", "ok"),
    ]


def test_from_chat_messages_skips_unknown_roles() -> None:
    history = ConversationHistory.from_chat_messages(
        [
            _FakeMsg(role="user", content="ok"),
            _FakeMsg(role="system", content="ignored"),
            _FakeMsg(role="bot", content="also ignored"),
            _FakeMsg(role="assistant", content="here"),
        ]
    )
    assert [t.role for t in history.turns] == ["student", "teacher"]


def test_from_chat_messages_normalizes_role_casing_and_whitespace() -> None:
    history = ConversationHistory.from_chat_messages(
        [
            _FakeMsg(role=" USER ", content="hi"),
            _FakeMsg(role="Assistant", content="hello"),
        ]
    )
    assert [(t.role, t.content) for t in history.turns] == [
        ("student", "hi"),
        ("teacher", "hello"),
    ]


def test_from_chat_messages_caps_to_last_n() -> None:
    msgs = [
        _FakeMsg(role="user", content="m1"),
        _FakeMsg(role="assistant", content="m2"),
        _FakeMsg(role="user", content="m3"),
        _FakeMsg(role="assistant", content="m4"),
        _FakeMsg(role="user", content="m5"),
    ]
    history = ConversationHistory.from_chat_messages(msgs, max_n=3)
    assert [t.content for t in history.turns] == ["m3", "m4", "m5"]


def test_from_chat_messages_max_n_zero_returns_empty() -> None:
    msgs = [_FakeMsg(role="user", content="m1"), _FakeMsg(role="assistant", content="m2")]
    history = ConversationHistory.from_chat_messages(msgs, max_n=0)
    assert history.is_empty()


def test_from_chat_messages_max_n_none_keeps_all() -> None:
    msgs = [_FakeMsg(role="user", content="m1"), _FakeMsg(role="assistant", content="m2")]
    history = ConversationHistory.from_chat_messages(msgs, max_n=None)
    assert [t.content for t in history.turns] == ["m1", "m2"]


def test_cap_returns_new_history() -> None:
    msgs = [
        _FakeMsg(role="user", content="m1"),
        _FakeMsg(role="assistant", content="m2"),
        _FakeMsg(role="user", content="m3"),
    ]
    original = ConversationHistory.from_chat_messages(msgs)
    capped = original.cap(1)
    assert [t.content for t in original.turns] == ["m1", "m2", "m3"]
    assert [t.content for t in capped.turns] == ["m3"]
    assert original is not capped


def test_cap_negative_returns_empty() -> None:
    h = ConversationHistory.from_chat_messages(
        [_FakeMsg(role="user", content="m1")]
    )
    assert h.cap(-5).is_empty()


def test_last_student_message_returns_most_recent_user_message() -> None:
    msgs = [
        _FakeMsg(role="user", content="first"),
        _FakeMsg(role="assistant", content="reply 1"),
        _FakeMsg(role="user", content="second"),
        _FakeMsg(role="assistant", content="reply 2"),
    ]
    h = ConversationHistory.from_chat_messages(msgs)
    assert h.last_student_message == "second"


def test_last_student_message_returns_none_when_only_teacher_messages() -> None:
    h = ConversationHistory.from_chat_messages(
        [_FakeMsg(role="assistant", content="hello")]
    )
    assert h.last_student_message is None


def test_last_student_message_returns_none_when_empty() -> None:
    h = ConversationHistory()
    assert h.last_student_message is None


def test_to_prompt_string_renders_labelled_lines() -> None:
    msgs = [
        _FakeMsg(role="user", content="What is TCP?"),
        _FakeMsg(role="assistant", content="TCP is a transport protocol."),
        _FakeMsg(role="user", content="And UDP?"),
    ]
    h = ConversationHistory.from_chat_messages(msgs)
    text = h.to_prompt_string()
    assert text == (
        "Student: What is TCP?\n"
        "Teacher: TCP is a transport protocol.\n"
        "Student: And UDP?"
    )


def test_to_prompt_string_returns_sentinel_when_empty() -> None:
    assert ConversationHistory().to_prompt_string() == "(no prior messages)"


def test_conversation_turn_rejects_empty_content() -> None:
    with pytest.raises(ValueError):
        ConversationTurn(role="student", content="")
    with pytest.raises(ValueError):
        ConversationTurn(role="teacher", content="   ")


def test_conversation_turn_rejects_unknown_role() -> None:
    with pytest.raises(ValueError):
        ConversationTurn(role="system", content="hello")  # type: ignore[arg-type]


def test_conversation_turn_strips_content_whitespace() -> None:
    t = ConversationTurn(role="student", content="  hi  ")
    assert t.content == "hi"
