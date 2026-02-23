from __future__ import annotations

import asyncio
import logging
from typing import Optional

from umabot.storage import Database, Queue

logger = logging.getLogger("umabot.scheduler")


class TaskScheduler:
    def __init__(self, *, db: Database, queue: Queue, poll_interval: float = 2.0) -> None:
        self._db = db
        self._queue = queue
        self._poll_interval = poll_interval
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                due_tasks = await asyncio.to_thread(self._db.lease_due_tasks, limit=20, lease_seconds=60)
                for task in due_tasks:
                    payload = {
                        "type": "task_run",
                        "task_id": int(task["id"]),
                    }
                    await self._queue.enqueue(
                        f"task:{task['id']}",
                        "system",
                        payload,
                    )
                    logger.debug("Scheduled task enqueued id=%s name=%s", task["id"], task["name"])
            except Exception as exc:
                logger.exception("Task scheduler loop failed: %s", exc)
            await asyncio.sleep(self._poll_interval)
