from __future__ import annotations

from uuid import uuid4

from app.schemas.course_info import CourseInfo, Lecture


def _make_lecture(
    *,
    slug: str,
    title: str,
    summary: str | None = None,
    description: str | None = None,
    ai_description: str | None = None,
    transcript_ready: bool = True,
) -> Lecture:
    return Lecture(
        id=uuid4(),
        content_id=uuid4(),
        slug=slug,
        title=title,
        description=description,
        ai_description=ai_description,
        summary=summary,
        transcript_ready=transcript_ready,
    )


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


# --- to_header: identity only ---


def test_to_header_is_name_and_description_only() -> None:
    lec = _make_lecture(
        slug="L1",
        title="Intro to Networking",
        ai_description="One-liner.",
        summary="A long summary of the lecture.",
    )
    course = CourseInfo(
        id=uuid4(),
        name="Computer Networks",
        description="Intro to networking.",
        summary="Course-level AI summary.",
        lectures=[lec],
    )

    text = course.to_header()

    assert "Course: Computer Networks" in text
    assert "Description: Intro to networking." in text
    # No lecture catalog, no AI summaries (course or lecture) leak into the header.
    assert "L1" not in text
    assert "Course summary:" not in text
    assert "One-liner." not in text
    assert "A long summary" not in text


# --- to_basic_info: lean catalog ---


def test_to_basic_info_contains_catalog_lines_without_full_summaries() -> None:
    lec = _make_lecture(
        slug="L1",
        title="Intro to Networking",
        ai_description="Covers the OSI model at a high level.",
        summary="A very long summary that should NOT appear in the basic view." * 50,
    )
    course = CourseInfo(
        id=uuid4(),
        name="Computer Networks",
        description="Intro to networking.",
        summary="Course-level AI summary.",
        lectures=[lec],
    )

    text = course.to_basic_info()

    assert "Course: Computer Networks" in text
    assert "Course summary: Course-level AI summary." in text
    # `slug: title — one-liner` and no bracketed slug (reserved for [N] citations).
    assert "L1: Intro to Networking — Covers the OSI model at a high level." in text
    assert "[L1]" not in text
    # The long lecture summary must not appear in the lean view.
    assert "A very long summary" not in text


def test_to_basic_info_falls_back_to_description_when_no_ai_description() -> None:
    lec = _make_lecture(
        slug="L1",
        title="Intro",
        description="Instructor blurb.",
        ai_description=None,
    )
    course = CourseInfo(id=uuid4(), name="Networks", description=None, lectures=[lec])

    text = course.to_basic_info()
    assert "L1: Intro — Instructor blurb." in text


def test_to_basic_info_flags_transcript_not_ready() -> None:
    lec = _make_lecture(slug="L1", title="TCP Deep Dive", transcript_ready=False)
    course = CourseInfo(id=uuid4(), name="Networks", description=None, lectures=[lec])

    text = course.to_basic_info()
    assert "transcript not ready" in text


# --- to_detailed_info: rich planning view ---


def test_to_detailed_info_includes_full_summaries() -> None:
    lec = _make_lecture(
        slug="L1",
        title="Intro",
        summary="This lecture introduces the OSI model in depth.",
    )
    course = CourseInfo(id=uuid4(), name="Networks", description=None, lectures=[lec])

    text = course.to_detailed_info()
    assert "L1: Intro" in text
    assert "summary: This lecture introduces the OSI model in depth." in text


def test_to_detailed_info_keeps_every_lecture_visible_under_budget() -> None:
    # Many lectures with long summaries: per-lecture budgeting must keep every
    # slug visible rather than bottom-truncating later lectures away.
    lectures = [
        _make_lecture(slug=f"L{i}", title=f"Lecture {i}", summary="x" * 4000)
        for i in range(1, 13)
    ]
    course = CourseInfo(id=uuid4(), name="Networks", description=None, lectures=lectures)

    text = course.to_detailed_info(summary_budget=6000)
    for i in range(1, 13):
        assert f"L{i}: Lecture {i}" in text


def test_to_detailed_info_omits_blank_optionals() -> None:
    lec = _make_lecture(slug="L1", title="Intro", description=None, summary=None)
    course = CourseInfo(id=uuid4(), name="Networks", description=None, lectures=[lec])

    text = course.to_detailed_info()
    assert "Description:" not in text
    assert "Course summary:" not in text
    assert "summary:" not in text
