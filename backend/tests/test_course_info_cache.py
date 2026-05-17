from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from cachetools import TTLCache

from app.schemas.course_info import CourseInfo
from app.services import course_info as course_info_module
from app.services.course_info import (
    build_course_info_cached,
    clear_course_info_cache,
    invalidate_course_info_cache,
)


class _StubCourse:
    """Minimal stand-in for `app.db.models.course.Course` — only `id` is read
    by the cache wrapper; the underlying `build_course_info` is mocked away."""

    def __init__(self, course_id):
        self.id = course_id


def _make_info(course_id) -> CourseInfo:
    return CourseInfo(id=course_id, name="Test Course", description=None, lectures=[])


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test sees a clean cache; otherwise tests bleed into each other."""
    clear_course_info_cache()
    yield
    clear_course_info_cache()


@pytest.mark.asyncio
async def test_cached_call_returns_same_value_without_re_running_builder() -> None:
    course_id = uuid4()
    course = _StubCourse(course_id)
    info = _make_info(course_id)

    mock_builder = AsyncMock(return_value=info)
    db = object()  # the wrapper passes `db` through but the mock ignores it

    with patch.object(course_info_module, "build_course_info", mock_builder):
        first = await build_course_info_cached(db=db, course=course)
        second = await build_course_info_cached(db=db, course=course)
        third = await build_course_info_cached(db=db, course=course)

    assert first is info
    assert second is first  # same object — cache hit, not a re-build
    assert third is first
    assert mock_builder.await_count == 1, "builder should only run on the first call"


@pytest.mark.asyncio
async def test_include_chapters_variants_are_cached_separately() -> None:
    course_id = uuid4()
    course = _StubCourse(course_id)
    info_with = _make_info(course_id)
    info_without = _make_info(course_id)

    async def fake_build(*, db, course, include_chapters=True):  # noqa: ARG001
        return info_with if include_chapters else info_without

    with patch.object(course_info_module, "build_course_info", side_effect=fake_build):
        a1 = await build_course_info_cached(db=None, course=course, include_chapters=True)
        a2 = await build_course_info_cached(db=None, course=course, include_chapters=True)
        b1 = await build_course_info_cached(db=None, course=course, include_chapters=False)
        b2 = await build_course_info_cached(db=None, course=course, include_chapters=False)

    assert a1 is info_with and a2 is info_with
    assert b1 is info_without and b2 is info_without
    # Distinct cache slots: two builder calls total (one per variant).


@pytest.mark.asyncio
async def test_invalidate_course_info_cache_drops_both_variants() -> None:
    course_id = uuid4()
    course = _StubCourse(course_id)

    call_count = {"n": 0}

    async def counting_build(*, db, course, include_chapters=True):  # noqa: ARG001
        call_count["n"] += 1
        return _make_info(course_id)

    with patch.object(course_info_module, "build_course_info", side_effect=counting_build):
        await build_course_info_cached(db=None, course=course, include_chapters=True)
        await build_course_info_cached(db=None, course=course, include_chapters=False)
        assert call_count["n"] == 2

        # Cached: no new builds.
        await build_course_info_cached(db=None, course=course, include_chapters=True)
        await build_course_info_cached(db=None, course=course, include_chapters=False)
        assert call_count["n"] == 2

        invalidate_course_info_cache(course_id)

        # Both variants should rebuild.
        await build_course_info_cached(db=None, course=course, include_chapters=True)
        await build_course_info_cached(db=None, course=course, include_chapters=False)
        assert call_count["n"] == 4


@pytest.mark.asyncio
async def test_invalidate_accepts_uuid_string() -> None:
    course_id = uuid4()
    course = _StubCourse(course_id)
    builder = AsyncMock(return_value=_make_info(course_id))

    with patch.object(course_info_module, "build_course_info", builder):
        await build_course_info_cached(db=None, course=course)
        await build_course_info_cached(db=None, course=course)
        assert builder.await_count == 1

        invalidate_course_info_cache(str(course_id))

        await build_course_info_cached(db=None, course=course)
        assert builder.await_count == 2


@pytest.mark.asyncio
async def test_invalidate_other_course_leaves_target_cached() -> None:
    course_a = _StubCourse(uuid4())
    course_b = _StubCourse(uuid4())

    builder = AsyncMock(side_effect=lambda **kw: _make_info(kw["course"].id))

    with patch.object(course_info_module, "build_course_info", builder):
        await build_course_info_cached(db=None, course=course_a)
        await build_course_info_cached(db=None, course=course_b)
        assert builder.await_count == 2

        invalidate_course_info_cache(course_b.id)

        await build_course_info_cached(db=None, course=course_a)  # still cached
        await build_course_info_cached(db=None, course=course_b)  # rebuild
        assert builder.await_count == 3


@pytest.mark.asyncio
async def test_ttl_expiry_triggers_rebuild() -> None:
    """Swap in a short-TTL cache and verify entries actually expire.

    `cachetools.TTLCache.timer` is read-only in 6.x, so we replace the whole
    cache instance for the duration of the test instead of poking the clock.
    """
    course_id = uuid4()
    course = _StubCourse(course_id)
    builder = AsyncMock(return_value=_make_info(course_id))

    short_ttl_cache: TTLCache = TTLCache(maxsize=8, ttl=0.05)
    with patch.object(course_info_module, "_COURSE_INFO_CACHE", short_ttl_cache):
        with patch.object(course_info_module, "build_course_info", builder):
            await build_course_info_cached(db=None, course=course)
            await build_course_info_cached(db=None, course=course)
            assert builder.await_count == 1

            # Sleep past the TTL — next call should miss and rebuild.
            await asyncio.sleep(0.1)
            await build_course_info_cached(db=None, course=course)
            assert builder.await_count == 2


def test_clear_course_info_cache_empties_state() -> None:
    cache = course_info_module._COURSE_INFO_CACHE
    cache[("a",)] = "stale"
    cache[("b",)] = "stale"
    assert len(cache) == 2
    clear_course_info_cache()
    assert len(cache) == 0
