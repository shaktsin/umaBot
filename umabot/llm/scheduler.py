"""Priority-ordered LLM request scheduler.

Wraps any LLMClient and serialises calls through a single asyncio consumer,
honouring the following priority levels:

    P0 = 0  Admin conversation — no delay, preempts everything.
    P1 = 1  Agent tool-loop iteration — normal, ~1 s between calls.
    P2 = 2  Background / listener events — at least ``p2_min_gap`` seconds
            between consecutive P2 calls so passive traffic does not starve
            the token budget.

Usage::

    scheduler = LLMScheduler(llm_client)
    scheduler.start()                          # starts background consumer

    # existing callers unchanged (default P1)
    response = await scheduler.generate(messages, tools=tools)

    # admin path (P0)
    response = await scheduler.generate(messages, priority=0)

    # background listener (P2)
    response = await scheduler.generate(messages, priority=2)

    await scheduler.stop()
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .base import LLMClient, LLMResponse

logger = logging.getLogger("umabot.llm.scheduler")

# Priority constants — use these instead of magic numbers
P0 = 0  # admin / urgent
P1 = 1  # agent loop (default)
P2 = 2  # background / listener


class LLMScheduler:
    """Priority queue wrapper around an LLMClient.

    Args:
        client:       The underlying LLM client to delegate to.
        p2_min_gap:   Minimum seconds between consecutive P2 calls (default 60).
    """

    def __init__(self, client: "LLMClient", p2_min_gap: float = 60.0) -> None:
        self._client = client
        self._p2_min_gap = p2_min_gap
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._seq = 0            # monotonically increasing tiebreaker (FIFO within priority)
        self._last_p2: float = 0.0
        self._consumer: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background consumer task."""
        if self._consumer is None or self._consumer.done():
            self._consumer = asyncio.create_task(self._run(), name="llm-scheduler")
            logger.info("LLMScheduler started p2_min_gap=%.0fs", self._p2_min_gap)

    async def stop(self) -> None:
        """Cancel the background consumer and drain in-flight futures."""
        if self._consumer and not self._consumer.done():
            self._consumer.cancel()
            try:
                await self._consumer
            except asyncio.CancelledError:
                pass
        self._consumer = None

    # ------------------------------------------------------------------
    # Public interface — drop-in replacement for LLMClient.generate()
    # ------------------------------------------------------------------

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        priority: int = P1,
    ) -> "LLMResponse":
        """Enqueue a generate request and await the result.

        Args:
            messages:  Chat messages list (same as LLMClient.generate).
            tools:     Tool spec list (same as LLMClient.generate).
            priority:  P0, P1, or P2.  Lower number = higher priority.
        """
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._seq += 1
        await self._queue.put((priority, self._seq, fut, messages, tools))
        logger.debug("LLMScheduler enqueued priority=%d qsize=%d", priority, self._queue.qsize())
        return await fut

    # ------------------------------------------------------------------
    # Internal consumer
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            try:
                priority, seq, fut, messages, tools = await self._queue.get()
            except asyncio.CancelledError:
                break

            # P2 rate limiting — enforce minimum gap between passive calls
            if priority == P2:
                now = time.monotonic()
                elapsed = now - self._last_p2
                if elapsed < self._p2_min_gap:
                    gap = self._p2_min_gap - elapsed
                    logger.info(
                        "LLMScheduler P2 throttle — waiting %.1fs before next background call",
                        gap,
                    )
                    await asyncio.sleep(gap)
                self._last_p2 = time.monotonic()

            if fut.done():
                # Caller cancelled while waiting in queue — skip silently
                self._queue.task_done()
                continue

            try:
                result = await self._client.generate(messages, tools=tools)
                if not fut.done():
                    fut.set_result(result)
            except Exception as exc:
                logger.error("LLMScheduler call failed priority=%d: %s", priority, exc)
                if not fut.done():
                    fut.set_exception(exc)
            finally:
                self._queue.task_done()
