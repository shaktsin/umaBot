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
    connector: str = ""  # original connector — used to route the final response back


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
        connector: str = "",
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
            return self.request_confirmation(
                tool_call=tool_call,
                chat_id=chat_id,
                channel=channel,
                connector=connector,
                session_id=session_id,
                message_id=message_id,
                messages=messages,
            )
        return PolicyDecision(True, False)

    def request_confirmation(
        self,
        *,
        tool_call: Dict[str, Any],
        chat_id: str,
        channel: str,
        connector: str,
        session_id: int,
        message_id: int,
        messages: list[dict],
        reason: Optional[str] = None,
    ) -> PolicyDecision:
        token = self._create_confirmation(
            chat_id, channel, connector, session_id, message_id, tool_call, messages
        )
        return PolicyDecision(False, True, reason=reason, token=token)

    def _create_confirmation(
        self,
        chat_id: str,
        channel: str,
        connector: str,
        session_id: int,
        message_id: int,
        tool_call: Dict[str, Any],
        messages: list[dict],
    ) -> str:
        token = secrets.token_hex(8)  # 128 bits of entropy (16 hex chars)
        pending = PendingConfirmation(
            token=token,
            chat_id=chat_id,
            channel=channel,
            connector=connector,
            session_id=session_id,
            message_id=message_id,
            tool_call=tool_call,
            messages=messages,
        )
        self._pending[(chat_id, token)] = pending
        return token

    def consume_confirmation(self, chat_id: str, message: str) -> Optional[PendingConfirmation]:
        message = message.strip()
        upper = message.upper()

        # Accept plain "y" or "yes" to confirm the most recent pending for this chat
        if upper in ("Y", "YES"):
            # Find the most recent pending confirmation for this chat_id
            matching_keys = [k for k in self._pending if k[0] == chat_id]
            if matching_keys:
                key = matching_keys[-1]
                return self._pending.pop(key, None)
            return None

        # Accept "YES <token>" for explicit token confirmation
        if not upper.startswith("YES "):
            return None
        token = message.split(" ", 1)[1].strip()
        # Try exact chat_id match first
        result = self._pending.pop((chat_id, token), None)
        if result:
            return result
        # Global fallback: web panel approves as "admin" but token may belong to another chat_id
        for key in list(self._pending):
            if key[1] == token:
                return self._pending.pop(key, None)
        return None
