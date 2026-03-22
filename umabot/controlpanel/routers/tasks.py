"""Task management API."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from umabot.controlpanel.deps import get_db
from umabot.storage.db import Database

router = APIRouter(prefix="/tasks", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    name: str
    prompt: str
    task_type: str  # one_time | periodic
    schedule: Dict[str, Any] = {}
    timezone: str = "UTC"
    next_run_at: Optional[str] = None


@router.get("")
async def list_tasks(
    status: Optional[str] = None,
    db: Database = Depends(get_db),
) -> List[Dict[str, Any]]:
    return db.list_tasks(status=status)


@router.post("")
async def create_task(req: CreateTaskRequest, db: Database = Depends(get_db)) -> Dict[str, Any]:
    task_id = db.create_task(
        name=req.name,
        prompt=req.prompt,
        task_type=req.task_type,
        schedule=req.schedule,
        timezone=req.timezone,
        next_run_at=req.next_run_at,
        created_by="web-panel",
    )
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=500, detail="Failed to create task")
    return task


@router.get("/{task_id}")
async def get_task(task_id: int, db: Database = Depends(get_db)) -> Dict[str, Any]:
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.delete("/{task_id}")
async def cancel_task(task_id: int, db: Database = Depends(get_db)) -> Dict[str, Any]:
    ok = db.cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found or already cancelled")
    return {"status": "cancelled", "task_id": task_id}


@router.get("/{task_id}/runs")
async def get_task_runs(task_id: int, db: Database = Depends(get_db), limit: int = 20) -> List[Dict[str, Any]]:
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
