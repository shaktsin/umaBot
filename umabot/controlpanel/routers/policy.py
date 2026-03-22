"""Policy, confirmations and audit log API."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from umabot.controlpanel.connector import GatewayConnector
from umabot.controlpanel.deps import get_broadcaster, get_config, get_connector, get_db, get_store
from umabot.controlpanel.events import EventBroadcaster
from umabot.controlpanel.store import PanelStore
from umabot.storage.db import Database

router = APIRouter(prefix="/policy", tags=["policy"])


class ConfirmRequest(BaseModel):
    token: str
    approved: bool


class PolicySettingsRequest(BaseModel):
    confirmation_strictness: str  # normal | strict
    shell_enabled: bool


@router.get("/pending")
async def list_pending(store: PanelStore = Depends(get_store)) -> List[Dict[str, Any]]:
    """List all pending tool confirmations."""
    return [
        {
            "token": c.token,
            "tool_name": c.tool_name,
            "args_preview": c.args_preview,
            "message": c.message,
            "chat_id": c.chat_id,
            "requested_at": c.requested_at,
        }
        for c in store.list_pending()
    ]


@router.post("/confirm")
async def confirm_action(
    req: ConfirmRequest,
    store: PanelStore = Depends(get_store),
    connector: GatewayConnector = Depends(get_connector),
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
) -> Dict[str, Any]:
    """Approve or deny a pending tool confirmation."""
    confirm = store.pending_confirmations.get(req.token)
    if not confirm:
        raise HTTPException(status_code=404, detail="Confirmation token not found or already resolved")

    response_text = f"YES {req.token}" if req.approved else f"NO {req.token}"
    await connector.send_message(response_text)
    store.remove_pending(req.token)

    await broadcaster.broadcast_event(
        "confirmation_resolved",
        {"token": req.token, "approved": req.approved},
    )
    return {"status": "approved" if req.approved else "denied", "token": req.token}


@router.get("/audit")
async def get_audit_log(
    db: Database = Depends(get_db),
    limit: int = 100,
    event_type: str = "",
) -> List[Dict[str, Any]]:
    """Fetch audit log entries."""
    with db._lock:
        if event_type:
            rows = db._conn.execute(
                "SELECT event_type, details_json, created_at FROM audit_log WHERE event_type = ? ORDER BY id DESC LIMIT ?",
                (event_type, limit),
            ).fetchall()
        else:
            rows = db._conn.execute(
                "SELECT event_type, details_json, created_at FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [
        {
            "event_type": r["event_type"],
            "details": json.loads(r["details_json"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.get("/settings")
async def get_policy_settings(config=Depends(get_config)) -> Dict[str, Any]:
    return {
        "confirmation_strictness": config.policy.confirmation_strictness,
        "shell_enabled": config.tools.shell_enabled,
    }
