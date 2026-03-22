"""Shared resilience primitives for Telegram connectors.

Provides:
  TelegramRateLimiter  — per-chat + global token buckets (Telegram send limits)
  CircuitBreaker       — per-chat circuit breaker; stops hammering a broken chat
  backoff_delay        — exponential backoff with jitter for reconnect loops
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, Tuple

logger = logging.getLogger("umabot.connectors.telegram_resilience")

# ---------------------------------------------------------------------------
# Telegram send rate limits (conservative, below documented maximums)
# ---------------------------------------------------------------------------
# Telegram allows ~30 msg/sec globally and ~1 msg/sec per individual chat.
# We stay well under to leave headroom.
_GLOBAL_MSG_PER_SEC = 20
_CHAT_MSG_PER_SEC   = 1


class TelegramRateLimiter:
    """Two-level token bucket: per-chat (1/s) and global (20/s).

    Call ``await limiter.acquire(chat_id)`` before every send.  Blocks until
    both buckets have capacity.  Thread-safe via asyncio.Lock.
    """

    def __init__(
        self,
        global_per_sec: int = _GLOBAL_MSG_PER_SEC,
        chat_per_sec: int = _CHAT_MSG_PER_SEC,
    ) -> None:
        self._global_per_sec = global_per_sec
        self._chat_per_sec   = chat_per_sec

        # Sliding 1-second windows: deque of timestamps
        self._global_window: Deque[float] = deque()
        self._chat_windows:  Dict[str, Deque[float]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, chat_id: str) -> None:
        """Block until both global and per-chat budgets allow a send."""
        async with self._lock:
            await self._wait_for_slot(self._global_window, self._global_per_sec, "global")
            chat_win = self._chat_windows.setdefault(chat_id, deque())
            await self._wait_for_slot(chat_win, self._chat_per_sec, chat_id)

    async def _wait_for_slot(
        self, window: Deque[float], limit: int, label: str
    ) -> None:
        while True:
            now = time.monotonic()
            # Expire entries older than 1 second
            while window and now - window[0] >= 1.0:
                window.popleft()
            if len(window) < limit:
                window.append(now)
                return
            wait = 1.0 - (now - window[0]) + 0.01
            logger.debug("Rate limit (%s) — waiting %.2fs", label, wait)
            self._lock.release()
            try:
                await asyncio.sleep(wait)
            finally:
                await self._lock.acquire()
            # Re-expire after sleep
            now = time.monotonic()
            while window and now - window[0] >= 1.0:
                window.popleft()


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class CBState(Enum):
    CLOSED   = "closed"    # normal operation
    OPEN     = "open"      # failing — reject calls
    HALF_OPEN = "half_open"  # testing recovery


@dataclass
class CircuitBreaker:
    """Per-resource circuit breaker.

    States:
      CLOSED    → normal; failure_count increments on each failure.
      OPEN      → after threshold failures; all calls rejected until
                  cooldown expires.
      HALF_OPEN → one probe call allowed; success resets to CLOSED,
                  failure resets cooldown and returns to OPEN.

    Args:
        failure_threshold: Consecutive failures before opening.
        cooldown_seconds:  How long to stay OPEN before trying again.
        name:              Label used in log messages.
    """
    failure_threshold: int = 5
    cooldown_seconds:  float = 300.0
    name:              str = "circuit"

    _state:         CBState = field(default=CBState.CLOSED, init=False, repr=False)
    _failure_count: int     = field(default=0, init=False, repr=False)
    _opened_at:     float   = field(default=0.0, init=False, repr=False)

    def is_open(self) -> bool:
        """Return True if calls should be blocked right now."""
        if self._state == CBState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_seconds:
                self._state = CBState.HALF_OPEN
                logger.info("Circuit %s → HALF_OPEN (probing)", self.name)
                return False  # allow one probe
            return True
        return False

    def record_success(self) -> None:
        if self._state in (CBState.HALF_OPEN, CBState.OPEN):
            logger.info("Circuit %s → CLOSED (recovered)", self.name)
        self._state = CBState.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._state == CBState.HALF_OPEN:
            # Probe failed — back to OPEN with fresh cooldown
            self._state = CBState.OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                "Circuit %s → OPEN (probe failed, cooldown=%.0fs)",
                self.name, self.cooldown_seconds,
            )
        elif self._failure_count >= self.failure_threshold:
            self._state = CBState.OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                "Circuit %s → OPEN after %d failures (cooldown=%.0fs)",
                self.name, self._failure_count, self.cooldown_seconds,
            )


class ChatCircuitBreakers:
    """Factory that holds one CircuitBreaker per chat_id."""

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 300.0) -> None:
        self._threshold = failure_threshold
        self._cooldown  = cooldown_seconds
        self._breakers:  Dict[str, CircuitBreaker] = {}

    def get(self, chat_id: str) -> CircuitBreaker:
        if chat_id not in self._breakers:
            self._breakers[chat_id] = CircuitBreaker(
                failure_threshold=self._threshold,
                cooldown_seconds=self._cooldown,
                name=f"chat:{chat_id}",
            )
        return self._breakers[chat_id]


# ---------------------------------------------------------------------------
# Exponential backoff helper
# ---------------------------------------------------------------------------

def backoff_delay(attempt: int, base: float = 2.0, cap: float = 60.0) -> float:
    """Return seconds to wait before the next reconnect attempt.

    Uses full-jitter exponential backoff:  random(0, min(cap, base * 2^attempt))
    """
    ceiling = min(cap, base * (2 ** attempt))
    delay = random.uniform(0, ceiling)
    return delay
