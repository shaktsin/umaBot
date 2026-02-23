from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Dict, Optional

from jsonschema.exceptions import ValidationError

from umabot.tools.registry import RISK_RED, ToolRegistry


@dataclass
class PolicyDecision:
    allowed: bool
    require_confirmation: bool
    reason: Optional[str] = None
    token: Optional[str] = None


@dataclass
class PendingConfirmation:
    token: str
    chat_id: str
    channel: str
    session_id: int
    message_id: int
    tool_call: Dict[str, Any]
    messages: list[dict]


class PolicyEngine:
    def __init__(self, tool_registry: ToolRegistry, strictness: str = "normal") -> None:
        self.tool_registry = tool_registry
        self.strictness = strictness
        self._pending: Dict[tuple[str, str], PendingConfirmation] = {}

    def evaluate(
        self,
        tool_call: Dict[str, Any],
        allowed_tools: list[str],
        *,
        chat_id: str,
        channel: str,
        session_id: int,
        message_id: int,
        messages: list[dict],
    ) -> PolicyDecision:
        name = tool_call.get("name")
        if not name:
            return PolicyDecision(False, False, reason="Missing tool name")
        if name not in allowed_tools:
            return PolicyDecision(False, False, reason=f"Tool {name} not allowed")
        tool = self.tool_registry.get(name)
        if not tool:
            return PolicyDecision(False, False, reason=f"Unknown tool: {name}")
        try:
            self.tool_registry.validate_args(name, tool_call.get("arguments", {}))
        except ValidationError as exc:
            return PolicyDecision(False, False, reason=f"Invalid args: {exc.message}")

        if tool.risk_level == RISK_RED:
            token = self._create_confirmation(
                chat_id, channel, session_id, message_id, tool_call, messages
            )
            return PolicyDecision(False, True, token=token)
        return PolicyDecision(True, False)

    def _create_confirmation(
        self,
        chat_id: str,
        channel: str,
        session_id: int,
        message_id: int,
        tool_call: Dict[str, Any],
        messages: list[dict],
    ) -> str:
        token = secrets.token_hex(3)
        pending = PendingConfirmation(
            token=token,
            chat_id=chat_id,
            channel=channel,
            session_id=session_id,
            message_id=message_id,
            tool_call=tool_call,
            messages=messages,
        )
        self._pending[(chat_id, token)] = pending
        return token

    def consume_confirmation(self, chat_id: str, message: str) -> Optional[PendingConfirmation]:
        message = message.strip()
        if not message.upper().startswith("YES "):
            return None
        token = message.split(" ", 1)[1].strip()
        return self._pending.pop((chat_id, token), None)
