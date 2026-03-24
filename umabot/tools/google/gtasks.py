"""Google Tasks tool handlers."""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("umabot.tools.google.gtasks")

_NOT_AUTH_MSG = (
    "Google Tasks is not authorised. Call the `google.authorize` tool first."
)


def _service(creds):
    from googleapiclient.discovery import build
    return build("tasks", "v1", credentials=creds, cache_discovery=False)


async def gtasks_list(args: Dict[str, Any], *, client_id, client_secret, db) -> str:
    """List tasks from a task list."""
    from .auth import get_credentials

    creds = get_credentials(client_id, client_secret, db)
    if not creds:
        return _NOT_AUTH_MSG

    tasklist_id = args.get("tasklist_id", "@default")
    show_completed = args.get("show_completed", False)
    max_results = min(int(args.get("max_results", 20)), 100)

    svc = _service(creds)
    resp = svc.tasks().list(
        tasklist=tasklist_id,
        showCompleted=show_completed,
        maxResults=max_results,
    ).execute()

    items = resp.get("items", [])
    if not items:
        return "No tasks found."

    lines = []
    for task in items:
        status = "✓" if task.get("status") == "completed" else "○"
        due = f" (due: {task['due']})" if task.get("due") else ""
        lines.append(f"{status} [{task['id']}] {task.get('title', '?')}{due}")
        if task.get("notes"):
            lines.append(f"    Notes: {task['notes'][:200]}")
    return "\n".join(lines)


async def gtasks_create(args: Dict[str, Any], *, client_id, client_secret, db) -> str:
    """Create a new task. Requires confirmation (RISK_YELLOW)."""
    from .auth import get_credentials

    creds = get_credentials(client_id, client_secret, db)
    if not creds:
        return _NOT_AUTH_MSG

    title = args.get("title", "").strip()
    if not title:
        return "title is required."

    task_body: dict = {"title": title}
    if args.get("notes"):
        task_body["notes"] = args["notes"]
    if args.get("due"):
        task_body["due"] = args["due"]  # RFC 3339

    svc = _service(creds)
    created = svc.tasks().insert(
        tasklist=args.get("tasklist_id", "@default"),
        body=task_body,
    ).execute()
    return f"Task created: {created.get('title')} (ID: {created['id']})"


async def gtasks_complete(args: Dict[str, Any], *, client_id, client_secret, db) -> str:
    """Mark a task as completed (RISK_GREEN)."""
    from .auth import get_credentials

    creds = get_credentials(client_id, client_secret, db)
    if not creds:
        return _NOT_AUTH_MSG

    task_id = args.get("task_id", "").strip()
    tasklist_id = args.get("tasklist_id", "@default")
    if not task_id:
        return "task_id is required."

    svc = _service(creds)
    existing = svc.tasks().get(tasklist=tasklist_id, task=task_id).execute()
    existing["status"] = "completed"
    updated = svc.tasks().update(
        tasklist=tasklist_id, task=task_id, body=existing
    ).execute()
    return f"Task '{updated.get('title')}' marked as completed."


async def gtasks_delete(args: Dict[str, Any], *, client_id, client_secret, db) -> str:
    """Delete a task. Requires approval (RISK_RED)."""
    from .auth import get_credentials

    creds = get_credentials(client_id, client_secret, db)
    if not creds:
        return _NOT_AUTH_MSG

    task_id = args.get("task_id", "").strip()
    tasklist_id = args.get("tasklist_id", "@default")
    if not task_id:
        return "task_id is required."

    svc = _service(creds)
    svc.tasks().delete(tasklist=tasklist_id, task=task_id).execute()
    return f"Task {task_id} deleted."
