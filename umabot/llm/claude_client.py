from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .base import LLMClient, LLMResponse, ToolCall

if TYPE_CHECKING:
    from .rate_limiter import TokenBucket


class ClaudeClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        model: str,
        rate_limiter: Optional["TokenBucket"] = None,
    ) -> None:
        super().__init__(rate_limiter=rate_limiter)
        self.api_key = api_key
        self.model = model

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        await self._throttle(messages, tools)

        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        filtered = [_sanitize_message(m) for m in messages if m.get("role") != "system"]
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 8192,
            "messages": filtered,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)

        name_map: Dict[str, str] = {}
        reverse_map: Dict[str, str] = {}
        if tools:
            payload["tools"] = [
                {
                    "name": _safe_tool_name(tool["name"], name_map, reverse_map),
                    "description": tool.get("description") or "",
                    "input_schema": tool["parameters"],
                }
                for tool in tools
            ]

        data = await self._http_post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload=payload,
        )

        content_blocks = data.get("content", [])
        text_parts = [block.get("text") for block in content_blocks if block.get("type") == "text"]
        content = "".join(part for part in text_parts if part)

        tool_calls = []
        for block in content_blocks:
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                if name in reverse_map:
                    name = reverse_map[name]
                tool_calls.append(
                    ToolCall(
                        name=name,
                        arguments=block.get("input") or {},
                        id=block.get("id"),
                    )
                )
        return LLMResponse(content=content, tool_calls=tool_calls)


def _safe_tool_name(name: str, name_map: Dict[str, str], reverse_map: Dict[str, str]) -> str:
    """Map tool names to Claude-safe identifiers (^[a-zA-Z0-9_-]{1,128}$)."""
    if name in name_map:
        return name_map[name]
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in name)
    if not safe:
        safe = "tool"
    candidate = safe
    suffix = 1
    while candidate in reverse_map and reverse_map[candidate] != name:
        suffix += 1
        candidate = f"{safe}_{suffix}"
    name_map[name] = candidate
    reverse_map[candidate] = name
    return candidate


def _sanitize_message(message: Dict[str, Any]) -> Dict[str, Any]:
    role = message.get("role")
    content = message.get("content") or ""

    # Tool result → Claude user/tool_result block format
    if role == "tool":
        tool_call_id = message.get("tool_call_id") or message.get("name", "tool")
        name = message.get("name", "tool")
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": content or f"Tool {name} returned no output.",
                }
            ],
        }

    # Assistant message with tool_calls → Claude tool_use block format
    if role == "assistant" and message.get("tool_calls"):
        blocks: List[Any] = []
        if content:
            blocks.append({"type": "text", "text": content})
        for call in message["tool_calls"]:
            fn = call.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": args,
                }
            )
        return {"role": "assistant", "content": blocks}

    return {"role": role, "content": content}
