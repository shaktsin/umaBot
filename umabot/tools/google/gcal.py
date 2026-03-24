"""Google Calendar tool handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger("umabot.tools.google.gcal")

_NOT_AUTH_MSG = (
    "Google Calendar is not authorised. Call the `google.authorize` tool first."
)


def _service(creds):
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


async def gcal_list_events(args: Dict[str, Any], *, client_id, client_secret, db) -> str:
    """List upcoming calendar events."""
    from .auth import get_credentials

    creds = get_credentials(client_id, client_secret, db)
    if not creds:
        return _NOT_AUTH_MSG

    calendar_id = args.get("calendar_id", "primary")
    max_results = min(int(args.get("max_results", 10)), 50)
    time_min = args.get("time_min") or datetime.now(timezone.utc).isoformat()
    if not time_min.endswith("Z") and "+" not in time_min:
        time_min += "Z"

    svc = _service(creds)
    resp = svc.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = resp.get("items", [])
    if not events:
        return "No upcoming events found."

    lines = []
    for ev in events:
        start = ev.get("start", {})
        start_str = start.get("dateTime") or start.get("date", "?")
        lines.append(
            f"ID: {ev['id']}\n"
            f"  Summary: {ev.get('summary', '(no title)')}\n"
            f"  Start: {start_str}\n"
            f"  Location: {ev.get('location', '')}"
        )
    return "\n\n".join(lines)


async def gcal_create_event(args: Dict[str, Any], *, client_id, client_secret, db) -> str:
    """Create a calendar event. Requires confirmation (RISK_YELLOW)."""
    from .auth import get_credentials

    creds = get_credentials(client_id, client_secret, db)
    if not creds:
        return _NOT_AUTH_MSG

    summary = args.get("summary", "").strip()
    start = args.get("start", "").strip()
    end = args.get("end", "").strip()
    if not summary or not start or not end:
        return "summary, start, and end are required (ISO 8601 format)."

    event_body: dict = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": args.get("timezone", "UTC")},
        "end": {"dateTime": end, "timeZone": args.get("timezone", "UTC")},
    }
    if args.get("description"):
        event_body["description"] = args["description"]
    if args.get("location"):
        event_body["location"] = args["location"]

    svc = _service(creds)
    created = svc.events().insert(
        calendarId=args.get("calendar_id", "primary"),
        body=event_body,
    ).execute()

    return f"Event created: {created.get('summary')} (ID: {created['id']})"


async def gcal_update_event(args: Dict[str, Any], *, client_id, client_secret, db) -> str:
    """Update an existing calendar event. Requires confirmation (RISK_YELLOW)."""
    from .auth import get_credentials

    creds = get_credentials(client_id, client_secret, db)
    if not creds:
        return _NOT_AUTH_MSG

    event_id = args.get("event_id", "").strip()
    calendar_id = args.get("calendar_id", "primary")
    if not event_id:
        return "event_id is required."

    svc = _service(creds)
    existing = svc.events().get(calendarId=calendar_id, eventId=event_id).execute()

    if args.get("summary"):
        existing["summary"] = args["summary"]
    if args.get("description"):
        existing["description"] = args["description"]
    if args.get("start"):
        existing["start"] = {"dateTime": args["start"], "timeZone": args.get("timezone", "UTC")}
    if args.get("end"):
        existing["end"] = {"dateTime": args["end"], "timeZone": args.get("timezone", "UTC")}
    if args.get("location"):
        existing["location"] = args["location"]

    updated = svc.events().update(
        calendarId=calendar_id, eventId=event_id, body=existing
    ).execute()
    return f"Event updated: {updated.get('summary')} (ID: {updated['id']})"


async def gcal_delete_event(args: Dict[str, Any], *, client_id, client_secret, db) -> str:
    """Delete a calendar event. Requires approval (RISK_RED)."""
    from .auth import get_credentials

    creds = get_credentials(client_id, client_secret, db)
    if not creds:
        return _NOT_AUTH_MSG

    event_id = args.get("event_id", "").strip()
    calendar_id = args.get("calendar_id", "primary")
    if not event_id:
        return "event_id is required."

    svc = _service(creds)
    svc.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return f"Event {event_id} deleted."
