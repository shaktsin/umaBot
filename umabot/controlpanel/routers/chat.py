"""Chat WebSocket and history API."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from umabot.controlpanel.connector import GatewayConnector, PANEL_CHAT_ID, PANEL_CHANNEL, PANEL_CONNECTOR
from umabot.controlpanel.deps import get_broadcaster, get_config, get_connector, get_db
from umabot.controlpanel.events import EventBroadcaster
from umabot.storage.db import Database

router = APIRouter(tags=["chat"])
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MiB


def _allowed_attachment_roots(config) -> List[Path]:
    roots: List[Path] = []
    workspaces = getattr(getattr(config, "tools", None), "workspaces", []) or []
    for ws in workspaces:
        try:
            roots.append(Path(ws.path).expanduser().resolve())
        except Exception:
            continue
    # Local development fallback when messages reference files under current repo.
    try:
        roots.append(Path.cwd().resolve())
    except Exception:
        pass
    return roots


def _resolve_attachment_path(raw_path: str, config) -> Path:
    path = (raw_path or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="Missing path")
    if path.startswith("sandbox:"):
        path = path[len("sandbox:") :]
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(status_code=400, detail="Path must be absolute")
    try:
        resolved = candidate.resolve()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path: {exc}") from exc
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    allowed_roots = _allowed_attachment_roots(config)
    if not any(root == resolved or root in resolved.parents for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Path is outside allowed roots")
    return resolved


@router.get("/chat/history")
async def get_chat_history(
    db: Database = Depends(get_db),
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return recent admin chat messages from DB."""
    session_id = db.get_or_create_session(PANEL_CHAT_ID, PANEL_CHANNEL, PANEL_CONNECTOR)
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
            "attachments": db.get_message_attachments(int(row["id"])),
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


@router.get("/chat/attachment")
async def get_chat_attachment(
    path: str = Query(..., min_length=1),
    config=Depends(get_config),
) -> Dict[str, Any]:
    """Load a local file as an inline attachment for preview in chat UI."""
    resolved = _resolve_attachment_path(path, config)
    size = resolved.stat().st_size
    if size <= 0:
        raise HTTPException(status_code=400, detail="File is empty")
    if size > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="File too large for inline preview")

    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {exc}") from exc
    mime_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    return {
        "filename": resolved.name,
        "mime_type": mime_type,
        "data": base64.b64encode(raw).decode("ascii"),
    }


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
