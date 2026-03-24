"""Register all Google Workspace tools into a ToolRegistry."""

from __future__ import annotations

import functools
from typing import Any, Callable, Dict

from umabot.tools.registry import RISK_GREEN, RISK_RED, RISK_YELLOW, Tool, ToolRegistry, ToolResult


def register_google_tools(registry: ToolRegistry, *, client_id: str, client_secret: str, db) -> None:
    """Register gmail.*, gcal.*, gtasks.*, and google.authorize tools."""

    if not client_id or not client_secret:
        import logging
        logging.getLogger("umabot.tools.google").warning(
            "Google tools disabled: client_id or client_secret not configured."
        )
        return

    def _wrap(fn: Callable, cid: str, csecret: str, database) -> Callable:
        """Wrap a handler to inject credentials + db."""
        async def _handler(args: Dict[str, Any]) -> ToolResult:
            import asyncio
            result = await asyncio.coroutine(fn)(args, client_id=cid, client_secret=csecret, db=database) \
                if asyncio.iscoroutinefunction(fn) \
                else await _run(fn, args, cid, csecret, database)
            return ToolResult(content=str(result))
        return _handler

    async def _run(fn, args, cid, csecret, database):
        return await fn(args, client_id=cid, client_secret=csecret, db=database)

    def wrap(fn):
        async def _handler(args):
            result = await fn(args, client_id=client_id, client_secret=client_secret, db=db)
            return ToolResult(content=str(result))
        return _handler

    # ------------------------------------------------------------------ #
    # google.authorize
    # ------------------------------------------------------------------ #
    from .auth import build_auth_url, is_authorized

    def _google_authorize_handler(cid, csecret, database, redirect_uri):
        async def _handler(args: Dict[str, Any]) -> ToolResult:
            if is_authorized(database):
                return ToolResult(content="Google is already authorised.")
            url, _ = build_auth_url(cid, redirect_uri)
            return ToolResult(
                content=(
                    f"To authorise Google access, open this URL in your browser:\n\n{url}\n\n"
                    "After granting access, you can retry your request."
                )
            )
        return _handler

    # Redirect URI depends on control panel config; use a sensible default.
    # The control panel registers its own callback at /oauth/google/callback.
    redirect_uri = getattr(db, "_google_redirect_uri", "http://localhost:5000/oauth/google/callback")

    registry.register(Tool(
        name="google.authorize",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_google_authorize_handler(client_id, client_secret, db, redirect_uri),
        risk_level=RISK_GREEN,
        description="Authorise Google Workspace access (Gmail, Calendar, Tasks). Call this when a Google tool says it is not authorised.",
    ))

    # ------------------------------------------------------------------ #
    # Gmail tools
    # ------------------------------------------------------------------ #
    from .gmail import gmail_list, gmail_read, gmail_send, gmail_search

    registry.register(Tool(
        name="gmail.list",
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query, e.g. 'in:inbox is:unread'"},
                "max_results": {"type": "integer", "default": 10},
            },
            "additionalProperties": False,
        },
        handler=wrap(gmail_list),
        risk_level=RISK_GREEN,
        description="List Gmail messages matching a query.",
    ))

    registry.register(Tool(
        name="gmail.read",
        schema={
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Gmail message ID"},
            },
            "required": ["message_id"],
            "additionalProperties": False,
        },
        handler=wrap(gmail_read),
        risk_level=RISK_GREEN,
        description="Read the full body of a Gmail message by ID.",
    ))

    registry.register(Tool(
        name="gmail.search",
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=wrap(gmail_search),
        risk_level=RISK_GREEN,
        description="Search Gmail using Gmail search syntax.",
    ))

    registry.register(Tool(
        name="gmail.send",
        schema={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
            "additionalProperties": False,
        },
        handler=wrap(gmail_send),
        risk_level=RISK_RED,
        description="Send an email via Gmail. Requires approval.",
    ))

    # ------------------------------------------------------------------ #
    # Google Calendar tools
    # ------------------------------------------------------------------ #
    from .gcal import gcal_list_events, gcal_create_event, gcal_update_event, gcal_delete_event

    registry.register(Tool(
        name="gcal.list_events",
        schema={
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "default": "primary"},
                "max_results": {"type": "integer", "default": 10},
                "time_min": {"type": "string", "description": "ISO 8601 start time (defaults to now)"},
            },
            "additionalProperties": False,
        },
        handler=wrap(gcal_list_events),
        risk_level=RISK_GREEN,
        description="List upcoming Google Calendar events.",
    ))

    registry.register(Tool(
        name="gcal.create_event",
        schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "start": {"type": "string", "description": "ISO 8601 datetime"},
                "end": {"type": "string", "description": "ISO 8601 datetime"},
                "description": {"type": "string"},
                "location": {"type": "string"},
                "timezone": {"type": "string", "default": "UTC"},
                "calendar_id": {"type": "string", "default": "primary"},
            },
            "required": ["summary", "start", "end"],
            "additionalProperties": False,
        },
        handler=wrap(gcal_create_event),
        risk_level=RISK_YELLOW,
        description="Create a new Google Calendar event. Requires confirmation.",
    ))

    registry.register(Tool(
        name="gcal.update_event",
        schema={
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "calendar_id": {"type": "string", "default": "primary"},
                "summary": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "description": {"type": "string"},
                "location": {"type": "string"},
                "timezone": {"type": "string", "default": "UTC"},
            },
            "required": ["event_id"],
            "additionalProperties": False,
        },
        handler=wrap(gcal_update_event),
        risk_level=RISK_YELLOW,
        description="Update an existing Google Calendar event. Requires confirmation.",
    ))

    registry.register(Tool(
        name="gcal.delete_event",
        schema={
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "calendar_id": {"type": "string", "default": "primary"},
            },
            "required": ["event_id"],
            "additionalProperties": False,
        },
        handler=wrap(gcal_delete_event),
        risk_level=RISK_RED,
        description="Delete a Google Calendar event. Requires approval.",
    ))

    # ------------------------------------------------------------------ #
    # Google Tasks tools
    # ------------------------------------------------------------------ #
    from .gtasks import gtasks_list, gtasks_create, gtasks_complete, gtasks_delete

    registry.register(Tool(
        name="gtasks.list",
        schema={
            "type": "object",
            "properties": {
                "tasklist_id": {"type": "string", "default": "@default"},
                "show_completed": {"type": "boolean", "default": False},
                "max_results": {"type": "integer", "default": 20},
            },
            "additionalProperties": False,
        },
        handler=wrap(gtasks_list),
        risk_level=RISK_GREEN,
        description="List Google Tasks.",
    ))

    registry.register(Tool(
        name="gtasks.create",
        schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "notes": {"type": "string"},
                "due": {"type": "string", "description": "RFC 3339 due date"},
                "tasklist_id": {"type": "string", "default": "@default"},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
        handler=wrap(gtasks_create),
        risk_level=RISK_YELLOW,
        description="Create a new Google Task. Requires confirmation.",
    ))

    registry.register(Tool(
        name="gtasks.complete",
        schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "tasklist_id": {"type": "string", "default": "@default"},
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        handler=wrap(gtasks_complete),
        risk_level=RISK_GREEN,
        description="Mark a Google Task as completed.",
    ))

    registry.register(Tool(
        name="gtasks.delete",
        schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "tasklist_id": {"type": "string", "default": "@default"},
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        handler=wrap(gtasks_delete),
        risk_level=RISK_RED,
        description="Delete a Google Task. Requires approval.",
    ))
