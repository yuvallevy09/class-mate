from __future__ import annotations

from uuid import uuid4

from app.schemas.course_info import CourseInfo, Lecture, LectureChapter


def _make_chapter(*, index: int, start: float, end: float, title: str) -> LectureChapter:
    return LectureChapter(
        id=uuid4(),
        chapter_index=index,
        title=title,
        description=None,
        start_sec=start,
        end_sec=end,
    )


def _make_lecture(
    *,
    slug: str,
    title: str,
    summary: str | None = None,
    description: str | None = None,
    duration_sec: float | None = None,
    transcript_ready: bool = True,
    chapters: list[LectureChapter] | None = None,
) -> Lecture:
    return Lecture(
        id=uuid4(),
        content_id=uuid4(),
        slug=slug,
        title=title,
        description=description,
        summary=summary,
        duration_sec=duration_sec,
        transcript_ready=transcript_ready,
        chapters=chapters or [],
    )


def test_lecture_chapter_duration_sec_is_non_negative() -> None:
    chap = _make_chapter(index=0, start=10.0, end=42.5, title="Intro")
    assert chap.duration_sec == 32.5

    inverted = _make_chapter(index=0, start=50.0, end=40.0, title="Bad")
    assert inverted.duration_sec == 0.0


def test_course_info_lecture_by_slug_exact_and_case_insensitive() -> None:
    a = _make_lecture(slug="L1", title="Intro")
    b = _make_lecture(slug="L2", title="TCP")
    course = CourseInfo(id=uuid4(), name="Networks", description=None, lectures=[a, b])

    assert course.lecture_by_slug("L1") is a
    assert course.lecture_by_slug("L2") is b
    assert course.lecture_by_slug("l2") is b
    assert course.lecture_by_slug("L99") is None
    assert course.lecture_by_slug("") is None


def test_course_info_lectures_by_slugs_dedupes_and_skips_unknown() -> None:
    a = _make_lecture(slug="L1", title="Intro")
    b = _make_lecture(slug="L2", title="TCP")
    c = _make_lecture(slug="L3", title="HTTP")
    course = CourseInfo(id=uuid4(), name="Networks", description=None, lectures=[a, b, c])

    out = course.lectures_by_slugs(["L3", "L1", "L1", "L99", "l3"])
    assert [lec.slug for lec in out] == ["L3", "L1"]


def test_to_prompt_string_contains_core_metadata() -> None:
    chapters = [
        _make_chapter(index=0, start=0.0, end=313.0, title="Welcome"),
        _make_chapter(index=1, start=313.0, end=1320.0, title="OSI Model"),
    ]
    lec = _make_lecture(
        slug="L1",
        title="Intro to Networking",
        description="Course overview.",
        summary="This lecture introduces the OSI model.",
        duration_sec=1320.0,
        transcript_ready=True,
        chapters=chapters,
    )
    course = CourseInfo(
        id=uuid4(),
        name="Computer Networks",
        description="Intro to networking.",
        lectures=[lec],
    )

    text = course.to_prompt_string()

    assert "Course: Computer Networks" in text
    assert "Description: Intro to networking." in text
    assert "[L1] Intro to Networking" in text
    assert "description: Course overview." in text
    assert "summary: This lecture introduces the OSI model." in text
    assert "0:00–5:13 Welcome" in text
    assert "5:13–22:00 OSI Model" in text


def test_to_prompt_string_flags_transcript_not_ready() -> None:
    lec = _make_lecture(slug="L1", title="TCP Deep Dive", transcript_ready=False)
    course = CourseInfo(id=uuid4(), name="Networks", description=None, lectures=[lec])

    text = course.to_prompt_string()
    assert "transcript not ready" in text


def test_to_prompt_string_toggles_hide_chapters_and_summaries() -> None:
    chapters = [_make_chapter(index=0, start=0.0, end=60.0, title="Welcome")]
    lec = _make_lecture(
        slug="L1",
        title="Intro",
        summary="A summary.",
        chapters=chapters,
    )
    course = CourseInfo(id=uuid4(), name="Networks", description=None, lectures=[lec])

    text = course.to_prompt_string(
        include_chapters=False,
        include_lecture_summaries=False,
    )
    assert "Welcome" not in text
    assert "summary:" not in text


def test_to_prompt_string_truncates_when_over_max_chars() -> None:
    lec = _make_lecture(
        slug="L1",
        title="Intro",
        summary="x" * 5000,
    )
    course = CourseInfo(id=uuid4(), name="Networks", description=None, lectures=[lec])

    text = course.to_prompt_string(max_chars=200)
    assert len(text) <= 200 + len("\n…(truncated)")
    assert "…(truncated)" in text


def test_to_prompt_string_omits_blank_optionals() -> None:
    lec = _make_lecture(slug="L1", title="Intro", description=None, summary=None)
    course = CourseInfo(id=uuid4(), name="Networks", description=None, lectures=[lec])

    text = course.to_prompt_string()
    assert "Description:" not in text
    assert "Course summary:" not in text
    assert "  description:" not in text
    assert "  summary:" not in text


def test_lecture_to_prompt_string_truncates_long_summary() -> None:
    lec = _make_lecture(slug="L1", title="Intro", summary="y" * 5000)
    rendered = lec.to_prompt_string(summary_max_chars=100)
    assert "…" in rendered
    summary_line = next(line for line in rendered.splitlines() if line.strip().startswith("summary:"))
    assert len(summary_line) <= 100 + len("  summary: ") + 1
