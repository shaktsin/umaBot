"""Chat WebSocket and history API."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from umabot.controlpanel.connector import GatewayConnector, PANEL_CHAT_ID, PANEL_CHANNEL, PANEL_CONNECTOR
from umabot.controlpanel.deps import get_broadcaster, get_connector, get_db
from umabot.controlpanel.events import EventBroadcaster
from umabot.storage.db import Database

router = APIRouter(tags=["chat"])


@router.get("/chat/history")
async def get_chat_history(
    db: Database = Depends(get_db),
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return recent admin chat messages from DB."""
    session_id = db.get_or_create_session(PANEL_CHAT_ID, PANEL_CHANNEL, PANEL_CONNECTOR)
    messages = db.list_recent_messages(session_id, limit=limit)
    # Also fetch tool calls for assistant messages
    enriched = []
    with db._lock:
        msg_rows = db._conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    msg_rows = list(reversed(msg_rows))
    for row in msg_rows:
        entry: Dict[str, Any] = {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"],
            "tool_calls": [],
        }
        if row["role"] == "assistant":
            with db._lock:
                tc_rows = db._conn.execute(
                    "SELECT tool_name, args_json, result_json, created_at FROM tool_calls WHERE message_id = ? ORDER BY id",
                    (row["id"],),
                ).fetchall()
            entry["tool_calls"] = [
                {
                    "tool_name": tc["tool_name"],
                    "args": json.loads(tc["args_json"] or "{}"),
                    "result": json.loads(tc["result_json"] or "null"),
                    "created_at": tc["created_at"],
                }
                for tc in tc_rows
            ]
        enriched.append(entry)
    return enriched


@router.websocket("/ws")
async def chat_ws(
    ws: WebSocket,
    connector: GatewayConnector = Depends(get_connector),
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
) -> None:
    """WebSocket endpoint for browser <-> panel <-> gateway chat and live events."""
    await ws.accept()
    await broadcaster.subscribe(ws)
    try:
        # Push current gateway status immediately
        await ws.send_json(
            {"type": "event", "name": "gateway_status", "data": {"connected": connector.store.gateway_connected}}
        )
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                # Send keepalive ping
                await ws.send_json({"type": "ping"})
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "chat":
                text = str(data.get("text", "")).strip()
                if text:
                    await connector.send_message(text)
            # Other types (subscribe, etc.) can be added here

    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unsubscribe(ws)
