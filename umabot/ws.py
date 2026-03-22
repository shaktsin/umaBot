from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from aiohttp import web


@dataclass
class WSClient:
    connector: str
    channel: str
    mode: str  # control | channel
    ws: web.WebSocketResponse


class ChannelHub:
    def __init__(self, on_status: Optional[Callable[[str, str, str, str], None]] = None) -> None:
        self._clients: Dict[str, Dict[int, WSClient]] = {}
        self._lock = asyncio.Lock()
        self._on_status = on_status

    async def register(self, ws: web.WebSocketResponse, connector: str, channel: str, mode: str) -> None:
        async with self._lock:
            bucket = self._clients.setdefault(channel, {})
            bucket[id(ws)] = WSClient(connector=connector, channel=channel, mode=mode, ws=ws)
        if self._on_status:
            self._on_status(connector, channel, mode, "connected")

    async def unregister(self, ws: web.WebSocketResponse) -> None:
        async with self._lock:
            for channel, bucket in list(self._clients.items()):
                client = bucket.pop(id(ws), None)
                if not bucket:
                    self._clients.pop(channel, None)
                if client and self._on_status:
                    self._on_status(client.connector, channel, client.mode, "disconnected")

    async def send(self, channel: str, connector: str, chat_id: str, text: str) -> bool:
        async with self._lock:
            bucket = self._clients.get(channel)
            if not bucket:
                return False
            client = next((c for c in bucket.values() if c.connector == connector), None)
            if not client:
                return False
            await client.ws.send_json({"type": "send", "chat_id": chat_id, "text": text})
            return True

    async def send_payload(self, channel: str, connector: str, chat_id: str, payload: dict) -> bool:
        """Send an arbitrary JSON payload to a specific connector.

        Used for structured messages like confirmation requests that carry
        more than just text (e.g. action buttons for Telegram inline keyboard).
        ``payload`` must include a ``"type"`` field.
        """
        async with self._lock:
            bucket = self._clients.get(channel)
            if not bucket:
                return False
            client = next((c for c in bucket.values() if c.connector == connector), None)
            if not client:
                return False
            await client.ws.send_json({**payload, "chat_id": chat_id})
            return True

    async def has_channel(self, channel: str) -> bool:
        async with self._lock:
            return channel in self._clients and bool(self._clients[channel])


class WebsocketGateway:
    def __init__(self, host: str, port: int, token: str, hub: ChannelHub):
        self.host = host
        self.port = port
        self.token = token
        self.hub = hub
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    async def start(self, on_event) -> None:
        app = web.Application()
        app.add_routes([web.get("/ws", lambda request: self._handle_ws(request, on_event))])
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def _handle_ws(self, request: web.Request, on_event) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        hello = await ws.receive_json()
        if not _is_valid_hello(hello, self.token):
            await ws.send_json({"type": "error", "message": "unauthorized"})
            await ws.close()
            return ws

        connector = str(hello.get("connector", ""))
        channel = str(hello.get("channel", ""))
        mode = str(hello.get("mode", "channel"))
        await self.hub.register(ws, connector=connector, channel=channel, mode=mode)
        await ws.send_json({"type": "ready"})

        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                data = msg.json()
                if data.get("type") == "event":
                    await on_event(connector, channel, mode, data)
            elif msg.type in {web.WSMsgType.CLOSE, web.WSMsgType.ERROR}:
                break

        await self.hub.unregister(ws)
        return ws


def _is_valid_hello(data: dict, token: str) -> bool:
    if not token:
        return False
    if data.get("type") != "hello" or data.get("token") != token:
        return False
    if not data.get("connector") or not data.get("channel"):
        return False
    return True
