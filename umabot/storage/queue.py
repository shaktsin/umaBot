from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from .db import Database


class Queue:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def enqueue(self, chat_id: str, channel: str, payload: Dict[str, Any]) -> int:
        return await asyncio.to_thread(self._db.enqueue_job, chat_id, channel, payload)

    async def claim(self, lease_seconds: int = 30) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._db.claim_job, lease_seconds)

    async def complete(self, job_id: int) -> None:
        await asyncio.to_thread(self._db.complete_job, job_id)

    async def fail(self, job_id: int, error: str) -> None:
        await asyncio.to_thread(self._db.fail_job, job_id, error)
