"""Shared mutable state for the control panel server."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Token pattern: 8+ lowercase hex chars (policy engine uses secrets.token_hex(8) = 16 chars)
_TOKEN_RE = re.compile(r"\b([0-9a-f]{8,})\b")


@dataclass
class PendingConfirmation:
    token: str
    tool_name: str
    args_preview: str
    message: str
    chat_id: str
    channel: str
    connector: str
    requested_at: float = field(default_factory=time.time)


def parse_token(text: str) -> Optional[str]:
    """Extract a confirmation token from a gateway message."""
    match = _TOKEN_RE.search(text)
    return match.group(1) if match else None


def parse_tool_name(text: str) -> str:
    """Best-effort extraction of tool name from confirmation message."""
    # Look for patterns like "tool: shell.run" or "`shell.run`"
    m = re.search(r"`([a-z_]+\.[a-z_]+)`", text)
    if m:
        return m.group(1)
    m = re.search(r"tool[:\s]+([a-z_]+\.[a-z_]+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return "unknown"


class PanelStore:
    def __init__(self) -> None:
        self.gateway_connected: bool = False
        self.pending_confirmations: Dict[str, PendingConfirmation] = {}
        self._start_time: float = time.time()

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self._start_time

    def add_pending(self, confirm: PendingConfirmation) -> None:
        self.pending_confirmations[confirm.token] = confirm

    def remove_pending(self, token: str) -> bool:
        return self.pending_confirmations.pop(token, None) is not None

    def list_pending(self) -> List[PendingConfirmation]:
        return list(self.pending_confirmations.values())

    def ingest_gateway_message(self, text: str, chat_id: str, channel: str, connector: str) -> Optional[str]:
        """
        If the message looks like a confirmation request, store it and return the token.
        Returns None if not a confirmation message.
        """
        token = parse_token(text)
        if token and ("confirm" in text.lower() or "yes" in text.lower() or "approve" in text.lower()):
            confirm = PendingConfirmation(
                token=token,
                tool_name=parse_tool_name(text),
                args_preview=text[:300],
                message=text,
                chat_id=chat_id,
                channel=channel,
                connector=connector,
            )
            self.add_pending(confirm)
            return token
        return None
