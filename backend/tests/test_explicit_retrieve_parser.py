from __future__ import annotations

import pytest

from app.rag.explicit_retrieve import _normalize_slug, _parse_timestamp_part


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("L1", "L1"),
        ("L2", "L2"),
        ("l3", "L3"),
        ("  L4  ", "L4"),
        ("L999", "L999"),
        ("L1234", "L1234"),
    ],
)
def test_normalize_slug_accepts_well_formed(raw: str, expected: str) -> None:
    assert _normalize_slug(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "L",
        "L99999",
        "X1",
        "1L",
        "L 1",
        "L1@",
        "L1.0",
        "garbage",
    ],
)
def test_normalize_slug_rejects_malformed(raw: str | None) -> None:
    assert _normalize_slug(raw) is None


@pytest.mark.parametrize(
    "raw,expected_sec",
    [
        ("0:00", 0),
        ("0:42", 42),
        ("27:36", 27 * 60 + 36),
        ("1:02:15", 3735),
        ("99:59", 99 * 60 + 59),
        ("1656", 1656),
        ("1656.5", 1656.5),
        ("0", 0),
        ("  27:36  ", 27 * 60 + 36),
    ],
)
def test_parse_timestamp_part_accepts_valid_forms(raw: str, expected_sec: float) -> None:
    out = _parse_timestamp_part(raw)
    assert out is not None
    assert out == pytest.approx(expected_sec)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "abc",
        "27:99",
        "99:99:99",
        "1:2:3:4",
        ":30",
        "-30",
        "-1:00",
        "1.0:00",
        "L1@27:36",  # combined form is now invalid — the slug field carries the slug
    ],
)
def test_parse_timestamp_part_rejects_invalid(raw: str | None) -> None:
    assert _parse_timestamp_part(raw) is None
