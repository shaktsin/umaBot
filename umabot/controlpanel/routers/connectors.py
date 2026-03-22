"""Connector management API."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from umabot.controlpanel.deps import get_config, get_db
from umabot.storage.db import Database

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.get("")
async def list_connectors(
    config=Depends(get_config),
    db: Database = Depends(get_db),
) -> List[Dict[str, Any]]:
    """List all configured connectors with their current status."""
    result = []
    for conn in getattr(config, "connectors", []) or []:
        name = conn.name if hasattr(conn, "name") else conn.get("name", "")
        conn_type = conn.type if hasattr(conn, "type") else conn.get("type", "unknown")
        with db._lock:
            row = db._conn.execute(
                "SELECT status, mode, channel, updated_at FROM connector_status WHERE connector = ? ORDER BY id DESC LIMIT 1",
                (name,),
            ).fetchone()
        result.append(
            {
                "name": name,
                "type": conn_type,
                "status": row["status"] if row else "unknown",
                "mode": row["mode"] if row else "channel",
                "channel": row["channel"] if row else "",
                "updated_at": row["updated_at"] if row else None,
            }
        )
    return result


@router.get("/{name}/logs")
async def get_connector_logs(
    name: str,
    db: Database = Depends(get_db),
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Recent status history for a connector."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT status, mode, channel, updated_at FROM connector_status WHERE connector = ? ORDER BY id DESC LIMIT ?",
            (name, limit),
        ).fetchall()
    return [dict(r) for r in rows]
