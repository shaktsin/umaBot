"""Gateway WebSocket connector for the control panel.

Connects to the umaBot gateway as a 'web-panel' control connector.
Forwards incoming assistant messages to browser clients via EventBroadcaster.
Forwards outgoing admin messages to the gateway.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import aiohttp

from umabot.controlpanel.events import EventBroadcaster
from umabot.controlpanel.store import PanelStore

logger = logging.getLogger("umabot.controlpanel.connector")

PANEL_CONNECTOR = "web-panel"
PANEL_CHANNEL = "web"
PANEL_CHAT_ID = "admin"


class GatewayConnector:
    """Maintains a persistent WebSocket connection to the umaBot gateway."""

    def __init__(self, ws_url: str, ws_token: str, store: PanelStore) -> None:
        self.ws_url = ws_url
        self.ws_token = ws_token
        self.store = store
        self._broadcaster: Optional[EventBroadcaster] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self, broadcaster: EventBroadcaster) -> None:
        self._broadcaster = broadcaster
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._connect_loop(), name="gateway-connector")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        self.store.gateway_connected = False

    async def send_message(self, text: str) -> None:
        """Send an admin chat message to the gateway."""
        if self._ws and not self._ws.closed:
            await self._ws.send_json(
                {
                    "type": "event",
                    "chat_id": PANEL_CHAT_ID,
                    "user_id": "admin",
                    "text": text,
                }
            )
        else:
            logger.warning("Cannot send message: not connected to gateway")

    async def _connect_loop(self) -> None:
        while True:
            try:
                await self._connect()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Gateway connector disconnected: %s — retrying in 5s", exc)
            self.store.gateway_connected = False
            if self._broadcaster:
                await self._broadcaster.broadcast_event("gateway_status", {"connected": False})
            await asyncio.sleep(5)

    async def _connect(self) -> None:
        assert self._session is not None
        async with self._session.ws_connect(self.ws_url, timeout=aiohttp.ClientTimeout(total=10)) as ws:
            self._ws = ws
            await ws.send_json(
                {
                    "type": "hello",
                    "token": self.ws_token,
                    "connector": PANEL_CONNECTOR,
                    "channel": PANEL_CHANNEL,
                    "mode": "control",
                }
            )
            msg = await ws.receive_json(timeout=5)
            if msg.get("type") != "ready":
                raise ConnectionError(f"Unexpected handshake response: {msg}")

            self.store.gateway_connected = True
            logger.info("Connected to umaBot gateway as web-panel")
            if self._broadcaster:
                await self._broadcaster.broadcast_event("gateway_status", {"connected": True})

            async for raw in ws:
                if raw.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(raw.data)
                    await self._handle_incoming(data)
                elif raw.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break

    async def _handle_incoming(self, data: dict) -> None:
        """Handle a message received from the gateway (assistant response)."""
        if data.get("type") != "send":
            return
        chat_id = str(data.get("chat_id", ""))
        text = str(data.get("text", ""))

        # Check if this is a confirmation request
        token = self.store.ingest_gateway_message(text, chat_id, PANEL_CHANNEL, PANEL_CONNECTOR)
        if token and self._broadcaster:
            confirm = self.store.pending_confirmations.get(token)
            if confirm:
                await self._broadcaster.broadcast_event(
                    "pending_confirmation",
                    {
                        "token": confirm.token,
                        "tool_name": confirm.tool_name,
                        "args_preview": confirm.args_preview,
                        "message": confirm.message,
                        "chat_id": confirm.chat_id,
                        "requested_at": confirm.requested_at,
                    },
                )

        attachments = data.get("attachments") or None
        if self._broadcaster:
            await self._broadcaster.broadcast_chat("assistant", text, chat_id, attachments=attachments)
