from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .base import LLMClient, LLMResponse, ToolCall

if TYPE_CHECKING:
    from .rate_limiter import TokenBucket

_REASONING_MODELS = ("o1", "o3", "o4")


def _is_reasoning_model(model: str) -> bool:
    """Return True for OpenAI o-series reasoning models."""
    name = model.lower()
    return any(name == prefix or name.startswith(prefix + "-") for prefix in _REASONING_MODELS)


class OpenAIClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        model: str,
        reasoning_effort: Optional[str] = None,
        rate_limiter: Optional["TokenBucket"] = None,
    ) -> None:
        super().__init__(rate_limiter=rate_limiter)
        self.api_key = api_key
        self.model = model
        self.reasoning_effort = reasoning_effort

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        await self._throttle(messages, tools)

        name_map: Dict[str, str] = {}
        reverse_map: Dict[str, str] = {}
        is_reasoning = _is_reasoning_model(self.model)
        safe_messages = _sanitize_messages_for_openai(
            messages, name_map, reverse_map, convert_system_role=is_reasoning
        )
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": safe_messages,
        }
        if is_reasoning and self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if tools:
            safe_tools = []
            for tool in tools:
                orig_name = tool["name"]
                safe_name = _safe_tool_name(orig_name, name_map, reverse_map)
                safe_tool = dict(tool)
                safe_tool["name"] = safe_name
                safe_tools.append({"type": "function", "function": safe_tool})
            payload["tools"] = safe_tools

        data = await self._http_post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
        )

        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        tool_calls = []
        for call in message.get("tool_calls", []) or []:
            fn = call.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            name = fn.get("name", "")
            if name in reverse_map:
                name = reverse_map[name]
            tool_calls.append(
                ToolCall(
                    name=name,
                    arguments=args,
                    id=call.get("id"),
                )
            )
        return LLMResponse(content=content, tool_calls=tool_calls)


def _sanitize_messages_for_openai(
    messages: List[Dict[str, Any]],
    name_map: Dict[str, str],
    reverse_map: Dict[str, str],
    convert_system_role: bool = False,
) -> List[Dict[str, Any]]:
    safe_messages: List[Dict[str, Any]] = []
    for message in messages:
        safe_message = dict(message)
        if convert_system_role and safe_message.get("role") == "system":
            safe_message["role"] = "developer"

        tool_calls = safe_message.get("tool_calls")
        if isinstance(tool_calls, list):
            safe_tool_calls = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    safe_tool_calls.append(call)
                    continue
                safe_call = dict(call)
                fn = safe_call.get("function")
                if isinstance(fn, dict):
                    safe_fn = dict(fn)
                    original_name = safe_fn.get("name")
                    if isinstance(original_name, str) and original_name:
                        safe_fn["name"] = _safe_tool_name(original_name, name_map, reverse_map)
                    safe_call["function"] = safe_fn
                safe_tool_calls.append(safe_call)
            safe_message["tool_calls"] = safe_tool_calls

        tool_name = safe_message.get("name")
        if safe_message.get("role") == "tool" and isinstance(tool_name, str) and tool_name:
            safe_message["name"] = _safe_tool_name(tool_name, name_map, reverse_map)

        safe_messages.append(safe_message)
    return safe_messages


def _safe_tool_name(name: str, name_map: Dict[str, str], reverse_map: Dict[str, str]) -> str:
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
