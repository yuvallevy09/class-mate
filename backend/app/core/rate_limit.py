from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Tuple

import asyncio


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_in_seconds: int


class FixedWindowRateLimiter:
    """
    Minimal in-memory fixed-window rate limiter.

    Notes:
    - Best-effort (per-process). Good enough for MVP webhook protection.
    - Keyed by (key, window_size_seconds).
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state: Dict[str, Tuple[int, int]] = {}
        # key -> (window_start_epoch_sec, count)

    async def hit(self, *, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = int(time.time())
        window_start = now - (now % int(window_seconds))
        reset_in = (window_start + int(window_seconds)) - now

        async with self._lock:
            start, count = self._state.get(key, (window_start, 0))
            if start != window_start:
                start, count = window_start, 0

            if count >= int(limit):
                self._state[key] = (start, count)
                return RateLimitResult(allowed=False, remaining=0, reset_in_seconds=int(reset_in))

            count += 1
            self._state[key] = (start, count)
            remaining = max(0, int(limit) - count)
            return RateLimitResult(allowed=True, remaining=int(remaining), reset_in_seconds=int(reset_in))




