from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import LLMClient, LLMResponse, ToolCall


class ClaudeClient(LLMClient):
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        filtered = [_sanitize_message(m) for m in messages if m.get("role") != "system"]
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": filtered,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)
        if tools:
            payload["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool.get("description") or "",
                    "input_schema": tool["parameters"],
                }
                for tool in tools
            ]
        data = await self._post_json(
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
                tool_calls.append(
                    ToolCall(
                        name=block.get("name", ""),
                        arguments=block.get("input") or {},
                    )
                )
        return LLMResponse(content=content, tool_calls=tool_calls)


def _sanitize_message(message: Dict[str, Any]) -> Dict[str, Any]:
    role = message.get("role")
    content = message.get("content") or ""
    if role == "tool":
        role = "user"
        name = message.get("name", "tool")
        content = f"Tool {name} result: {content}"
    return {"role": role, "content": content}
