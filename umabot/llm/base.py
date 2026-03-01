from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError


logger = logging.getLogger("umabot.llm")


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, Any]
    id: Optional[str] = None


@dataclass
class LLMResponse:
    content: str
    tool_calls: List[ToolCall]


class LLMClient:
    async def generate(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> LLMResponse:
        raise NotImplementedError

    async def _post_json(self, url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = Request(url, data=body, headers=headers)
        return await asyncio.to_thread(self._read_json, req)

    def _read_json(self, req: Request) -> Dict[str, Any]:
        try:
            with urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            logger.error("LLM HTTP error status=%s body_len=%s", exc.code, len(body))
            raise
