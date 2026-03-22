"""Dashboard API: status and aggregate stats."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from umabot.controlpanel.deps import get_config, get_db, get_skill_registry, get_store
from umabot.controlpanel.store import PanelStore
from umabot.storage.db import Database

router = APIRouter(tags=["dashboard"])


@router.get("/status")
async def get_status(
    config=Depends(get_config),
    store: PanelStore = Depends(get_store),
    db: Database = Depends(get_db),
) -> Dict[str, Any]:
    """Gateway and panel status."""
    # Connector health from DB (latest per connector)
    connectors = _connector_health(db, config)
    return {
        "gateway_connected": store.gateway_connected,
        "uptime_seconds": store.uptime_seconds,
        "panel_version": "0.1.0",
        "pending_confirmations": len(store.list_pending()),
        "connectors": connectors,
    }


@router.get("/stats")
async def get_stats(
    config=Depends(get_config),
    store: PanelStore = Depends(get_store),
    db: Database = Depends(get_db),
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    """Aggregate statistics for the dashboard."""
    # Message counts
    msg_1h = _count_messages(db, hours=1)
    msg_24h = _count_messages(db, hours=24)

    # Skills
    try:
        skills_count = len(skill_registry.list())
    except Exception:
        skills_count = 0

    # Active tasks
    try:
        active_tasks = len(db.list_tasks(status="active"))
    except Exception:
        active_tasks = 0

    return {
        "messages_1h": msg_1h,
        "messages_24h": msg_24h,
        "skills_loaded": skills_count,
        "active_tasks": active_tasks,
        "pending_confirmations": len(store.list_pending()),
        "connectors_total": len(getattr(config, "connectors", []) or []),
        "connectors_active": sum(
            1 for c in _connector_health(db, config) if c["status"] == "connected"
        ),
    }


@router.get("/activity")
async def get_activity(db: Database = Depends(get_db), limit: int = 50) -> List[Dict[str, Any]]:
    """Recent audit log entries for the activity feed."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT event_type, details_json, created_at FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    import json
    return [
        {"event_type": r["event_type"], "details": json.loads(r["details_json"]), "created_at": r["created_at"]}
        for r in rows
    ]


def _connector_health(db: Database, config) -> List[Dict[str, Any]]:
    """Latest status for each configured connector."""
    result = []
    configured = getattr(config, "connectors", []) or []
    for conn in configured:
        name = conn.name if hasattr(conn, "name") else conn.get("name", "")
        conn_type = conn.type if hasattr(conn, "type") else conn.get("type", "unknown")
        with db._lock:
            row = db._conn.execute(
                "SELECT status, updated_at FROM connector_status WHERE connector = ? ORDER BY id DESC LIMIT 1",
                (name,),
            ).fetchone()
        status = row["status"] if row else "unknown"
        updated_at = row["updated_at"] if row else None
        result.append(
            {"name": name, "type": conn_type, "status": status, "updated_at": updated_at}
        )
    return result


def _count_messages(db: Database, hours: int) -> int:
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat() + "Z"
    with db._lock:
        row = db._conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE created_at >= ?", (since,)
        ).fetchone()
    return row["cnt"] if row else 0
