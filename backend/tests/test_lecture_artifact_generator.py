"""Unit tests for `lecture_artifact_generator` post-processing.

Pure-function tests — no DB / network. They verify the helpers that shape the
LM output before persistence:

- `_normalize_summary_timestamps`: keeps the `[#M:SS]` contract the frontend
  linkifier depends on (and repairs common LM deviations).
- `_clean_title` / `_clean_desc`: normalize and bound the title/description.
"""

from __future__ import annotations

from app.ai.lecture_artifact_generator import (
    _clean_desc,
    _clean_title,
    _normalize_summary_timestamps,
    _ts_to_total_minutes,
)


# --- _ts_to_total_minutes ---


def test_ts_passthrough_minutes_seconds() -> None:
    assert _ts_to_total_minutes("0:04") == "0:04"
    assert _ts_to_total_minutes("15:15") == "15:15"


def test_ts_folds_hours_into_total_minutes() -> None:
    assert _ts_to_total_minutes("1:15:20") == "75:20"
    assert _ts_to_total_minutes("2:00:00") == "120:00"


def test_ts_rejects_invalid() -> None:
    assert _ts_to_total_minutes("0:60") is None  # seconds out of range
    assert _ts_to_total_minutes("1:75:00") is None  # minutes out of range in H:MM:SS
    assert _ts_to_total_minutes("abc") is None
    assert _ts_to_total_minutes("12") is None


# --- _normalize_summary_timestamps ---


def test_normalize_adds_missing_hash() -> None:
    assert _normalize_summary_timestamps("See [0:04].") == "See [#0:04]."


def test_normalize_idempotent_on_canonical() -> None:
    text = "Intro [#0:04] and recap [#12:30]."
    assert _normalize_summary_timestamps(text) == text


def test_normalize_folds_hour_form() -> None:
    assert _normalize_summary_timestamps("Late point [#1:15:20].") == "Late point [#75:20]."


def test_normalize_mixed_list() -> None:
    out = _normalize_summary_timestamps("Moments [0:04, #6:06, 15:15].")
    assert out == "Moments [#0:04, #6:06, #15:15]."


def test_normalize_leaves_numeric_citations_untouched() -> None:
    text = "A claim [1] and another [2]."
    assert _normalize_summary_timestamps(text) == text


def test_normalize_leaves_non_timestamp_brackets_untouched() -> None:
    text = "Reference [Source: L1 (0:04-1:30)] stays as-is."
    assert _normalize_summary_timestamps(text) == text


def test_normalize_empty_string() -> None:
    assert _normalize_summary_timestamps("") == ""


# --- _clean_title ---


def test_clean_title_strips_quotes() -> None:
    assert _clean_title('"Server vs Serverless"') == "Server vs Serverless"


def test_clean_title_strips_json_label_prefix() -> None:
    assert _clean_title("JSON Title: Server vs Serverless") == "Server vs Serverless"
    assert _clean_title("Title - Intro to Networking") == "Intro to Networking"


def test_clean_title_caps_to_five_words() -> None:
    out = _clean_title("One Two Three Four Five Six Seven")
    assert out == "One Two Three Four Five"


def test_clean_title_collapses_whitespace_and_trailing_punct() -> None:
    assert _clean_title("  Intro   to  Networking.  ") == "Intro to Networking"


# --- _clean_desc ---


def test_clean_desc_collapses_whitespace() -> None:
    assert _clean_desc("This  lecture\n covers\tservers.") == "This lecture covers servers."


def test_clean_desc_caps_length() -> None:
    long = "word " * 300
    assert len(_clean_desc(long)) <= 600
