from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .base import LLMClient, LLMResponse, ToolCall, _MAX_DELAY

if TYPE_CHECKING:
    from .rate_limiter import TokenBucket


class GeminiClient(LLMClient):
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
        contents = [
            {
                "role": "user" if m["role"] == "user" else "model",
                "parts": [{"text": m["content"]}],
            }
            for m in filtered
        ]
        payload: Dict[str, Any] = {"contents": contents}
        if system_parts:
            payload["system_instruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
        if tools:
            payload["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": tool["name"],
                            "description": tool.get("description") or "",
                            "parameters": tool["parameters"],
                        }
                        for tool in tools
                    ]
                }
            ]

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models"
            f"/{self.model}:generateContent?key={self.api_key}"
        )
        data = await self._http_post(
            url,
            headers={"Content-Type": "application/json"},
            payload=payload,
        )

        candidate = (data.get("candidates") or [{}])[0]
        content_parts = (candidate.get("content") or {}).get("parts", [])
        text_parts = [part.get("text") for part in content_parts if "text" in part]
        content = "".join(part for part in text_parts if part)

        tool_calls = []
        for part in content_parts:
            if "functionCall" in part:
                call = part["functionCall"]
                tool_calls.append(
                    ToolCall(
                        name=call.get("name", ""),
                        arguments=call.get("args") or {},
                    )
                )
        return LLMResponse(content=content, tool_calls=tool_calls)

    # ------------------------------------------------------------------
    # Gemini embeds retry delay in the JSON error body
    # ------------------------------------------------------------------

    def _retry_delay_from_body(
        self,
        body: Dict[str, Any],
        attempt: int,
        base: float,
    ) -> float:
        """Gemini error bodies may contain retryDelay e.g. '30s'."""
        try:
            details = body.get("error", {}).get("details", [])
            for detail in details:
                raw = detail.get("retryDelay", "")
                if raw:
                    return min(float(raw.rstrip("s")), _MAX_DELAY)
        except Exception:
            pass
        return super()._retry_delay_from_body(body, attempt, base)


def _sanitize_message(message: Dict[str, Any]) -> Dict[str, Any]:
    role = message.get("role")
    content = message.get("content") or ""
    if role == "tool":
        role = "user"
        name = message.get("name", "tool")
        content = f"Tool {name} result: {content}"
    if role not in {"user", "assistant"}:
        role = "user"
    return {"role": role, "content": content}
