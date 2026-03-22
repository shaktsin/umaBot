"""Provider-agnostic LLM client base.

All provider clients (Claude, OpenAI, Gemini, …) inherit from ``LLMClient``
and get the following for free:

- ``_http_post``       — async aiohttp POST with exponential-backoff retry on
                         rate-limit responses (HTTP 429 / 529).
- ``_retry_delay``     — reads the ``retry-after`` header; override per provider.
- ``_retry_delay_from_body`` — parses retry delay from JSON body; override for
                         providers that embed it there (e.g. Gemini).
- ``_throttle``        — pre-request token-budget check via an optional
                         ``TokenBucket``; no-op when no bucket is configured.
- ``estimate_tokens``  — cheap chars/4 heuristic; good enough for throttling.
- ``compress_tool_output`` — structured truncation that keeps first + last lines
                         and strips ANSI escape codes.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from .rate_limiter import TokenBucket

logger = logging.getLogger("umabot.llm")

# Maximum retries on rate-limit responses before propagating the error
_MAX_RETRIES = 5
# Base delay (seconds) used when no retry-after header is present
_BASE_DELAY = 2.0
# Hard cap on any single retry wait
_MAX_DELAY = 120.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field  # noqa: E402 — after logger


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, Any]
    id: Optional[str] = None


@dataclass
class LLMResponse:
    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Base client
# ---------------------------------------------------------------------------

class LLMClient:
    """Abstract base for all LLM provider clients.

    Args:
        rate_limiter: Optional shared ``TokenBucket``.  When provided,
            ``_throttle`` is called before every request so the caller
            never exceeds the configured tokens-per-minute budget.
    """

    def __init__(self, rate_limiter: Optional["TokenBucket"] = None) -> None:
        self._rate_limiter = rate_limiter

    # ------------------------------------------------------------------
    # Public interface — subclasses implement this
    # ------------------------------------------------------------------

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # HTTP layer — shared by all providers
    # ------------------------------------------------------------------

    async def _http_post(
        self,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Async POST with exponential-backoff retry on 429 / 529.

        Replaces the old ``_post_json`` / ``asyncio.to_thread`` approach so
        rate-limit sleeps never block the event loop.
        """
        timeout = aiohttp.ClientTimeout(total=180)

        for attempt in range(_MAX_RETRIES):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        url, headers=headers, json=payload
                    ) as resp:
                        if resp.status in (429, 529):
                            body: Dict[str, Any] = {}
                            try:
                                body = await resp.json(content_type=None)
                            except Exception:
                                pass
                            delay = self._retry_delay_from_body(body, attempt, _BASE_DELAY)
                            if delay == _BASE_DELAY * (2 ** attempt):
                                # body gave no info — fall back to header
                                delay = self._retry_delay(resp, attempt, _BASE_DELAY)
                            logger.warning(
                                "Rate limited HTTP %d (attempt %d/%d), waiting %.1fs",
                                resp.status,
                                attempt + 1,
                                _MAX_RETRIES,
                                delay,
                            )
                            await asyncio.sleep(delay)
                            continue

                        if resp.status >= 400:
                            text = await resp.text()
                            logger.error(
                                "LLM HTTP error status=%d body=%s", resp.status, text[:500]
                            )
                            resp.raise_for_status()

                        return await resp.json(content_type=None)

            except aiohttp.ClientResponseError as exc:
                if exc.status in (429, 529) and attempt < _MAX_RETRIES - 1:
                    delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                    logger.warning("Rate limit (ClientResponseError) waiting %.1fs", delay)
                    await asyncio.sleep(delay)
                    continue
                raise

        raise RuntimeError(
            f"LLM request to {url} failed after {_MAX_RETRIES} retries"
        )

    # ------------------------------------------------------------------
    # Retry-delay hooks — providers may override
    # ------------------------------------------------------------------

    def _retry_delay(
        self,
        response: aiohttp.ClientResponse,
        attempt: int,
        base: float,
    ) -> float:
        """Extract retry delay from response headers (default: retry-after)."""
        header = response.headers.get("retry-after") or response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), _MAX_DELAY)
            except ValueError:
                pass
        return min(base * (2 ** attempt), _MAX_DELAY)

    def _retry_delay_from_body(
        self,
        body: Dict[str, Any],
        attempt: int,
        base: float,
    ) -> float:
        """Extract retry delay from response JSON body.

        Default implementation returns the standard exponential backoff.
        Providers (e.g. Gemini) that embed the delay in the JSON body
        should override this method.
        """
        return min(base * (2 ** attempt), _MAX_DELAY)

    # ------------------------------------------------------------------
    # Token throttle — no-op when no bucket configured
    # ------------------------------------------------------------------

    async def _throttle(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Acquire token budget before sending a request."""
        if self._rate_limiter is not None:
            tokens = estimate_tokens(messages, tools)
            await self._rate_limiter.acquire(tokens)

    # ------------------------------------------------------------------
    # Legacy compatibility shim — kept so old callers don't break
    # ------------------------------------------------------------------

    async def _post_json(
        self,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Deprecated: use _http_post instead."""
        return await self._http_post(url, headers, payload)


# ---------------------------------------------------------------------------
# Provider-agnostic utilities
# ---------------------------------------------------------------------------

def estimate_tokens(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """Rough token estimate: 1 token ≈ 4 characters.

    Accurate enough for throttling decisions without requiring a tokeniser.
    Adds 200 tokens of overhead for roles, JSON structure, and model metadata.
    """
    total = sum(len(str(m.get("content") or "")) for m in messages) // 4
    if tools:
        total += len(json.dumps(tools)) // 4
    total += 200
    return total


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHF]")


def compress_tool_output(text: str, max_chars: int = 800) -> str:
    """Compress shell / tool output for storage in agent message history.

    - Strips ANSI escape codes.
    - Keeps the first 5 lines (command echo, setup) and last 10 lines
      (results, errors) with a truncation marker in between.
    - Hard-caps the result at ``max_chars``.
    """
    text = _ANSI_RE.sub("", text)
    if len(text) <= max_chars:
        return text
    lines = text.splitlines()
    if len(lines) <= 15:
        return text[:max_chars]
    head = lines[:5]
    tail = lines[-10:]
    compressed = "\n".join(head + ["...[middle truncated]..."] + tail)
    return compressed[:max_chars]


# Needed for asyncio.sleep inside _http_post
import asyncio  # noqa: E402 — intentional late import to keep dataclasses at top
