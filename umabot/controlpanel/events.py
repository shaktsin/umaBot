"""WebSocket event broadcaster for the control panel."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Set

from fastapi import WebSocket

logger = logging.getLogger("umabot.controlpanel.events")


class EventBroadcaster:
    """Broadcasts events from the gateway connector to all connected browser clients."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def unsubscribe(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, data: Dict[str, Any]) -> None:
        """Send JSON data to all connected browser clients."""
        msg = json.dumps(data)
        async with self._lock:
            dead: Set[WebSocket] = set()
            for ws in self._clients:
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.add(ws)
            self._clients -= dead

    async def broadcast_chat(self, role: str, content: str, chat_id: str = "admin") -> None:
        await self.broadcast({"type": "chat", "role": role, "content": content, "chat_id": chat_id})

    async def broadcast_event(self, name: str, data: Dict[str, Any]) -> None:
        await self.broadcast({"type": "event", "name": name, "data": data})
