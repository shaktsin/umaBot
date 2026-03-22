"""Provider-agnostic token-rate limiter for LLM API calls.

Uses a sliding 60-second window to track tokens sent and pre-emptively
waits when the next request would exceed the configured per-minute budget.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Deque, Tuple

logger = logging.getLogger("umabot.llm.rate_limiter")


class TokenBucket:
    """Sliding-window token rate limiter.

    Tracks tokens consumed in the last 60 seconds and blocks callers
    until enough budget is available.  Thread-safe via asyncio.Lock.

    Args:
        tokens_per_minute: Maximum tokens allowed per 60-second window.
    """

    def __init__(self, tokens_per_minute: int) -> None:
        self._limit = tokens_per_minute
        self._window: Deque[Tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int) -> None:
        """Block until ``tokens`` fit within the current 60-second window."""
        async with self._lock:
            while True:
                now = time.monotonic()
                # Expire entries older than 60 s
                while self._window and now - self._window[0][0] >= 60.0:
                    self._window.popleft()

                used = sum(t for _, t in self._window)
                if used + tokens <= self._limit:
                    self._window.append((now, tokens))
                    return

                # Wait until the oldest entry expires
                wait = 60.0 - (now - self._window[0][0]) + 0.1
                logger.info(
                    "Token budget %d/%d used — waiting %.1fs before next request",
                    used,
                    self._limit,
                    wait,
                )
                # Release lock while sleeping so other coroutines can progress
                self._lock.release()
                try:
                    await asyncio.sleep(wait)
                finally:
                    await self._lock.acquire()
