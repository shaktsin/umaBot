"""Gmail tool handlers."""

from __future__ import annotations

import base64
import email.mime.text
import logging
from email.utils import parseaddr
from typing import Any, Dict, Optional

logger = logging.getLogger("umabot.tools.google.gmail")

_NOT_AUTH_MSG = (
    "Gmail is not authorised. Call the `google.authorize` tool first to get a login link."
)


def _service(creds):
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _fmt_headers(headers: list) -> dict:
    return {h["name"]: h["value"] for h in headers}


async def gmail_list(args: Dict[str, Any], *, client_id, client_secret, db) -> str:
    """List recent emails."""
    from .auth import get_credentials

    creds = get_credentials(client_id, client_secret, db)
    if not creds:
        return _NOT_AUTH_MSG

    query = args.get("query", "in:inbox")
    max_results = min(int(args.get("max_results", 10)), 50)

    svc = _service(creds)
    resp = svc.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    messages = resp.get("messages", [])
    if not messages:
        return "No messages found."

    lines = []
    for msg in messages:
        detail = svc.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["Subject", "From", "Date"]
        ).execute()
        hdrs = _fmt_headers(detail.get("payload", {}).get("headers", []))
        lines.append(
            f"ID: {msg['id']}\n"
            f"  From: {hdrs.get('From', '?')}\n"
            f"  Subject: {hdrs.get('Subject', '(no subject)')}\n"
            f"  Date: {hdrs.get('Date', '?')}"
        )

    return "\n\n".join(lines)


async def gmail_read(args: Dict[str, Any], *, client_id, client_secret, db) -> str:
    """Read full email body by message ID."""
    from .auth import get_credentials
    from googleapiclient.errors import HttpError

    creds = get_credentials(client_id, client_secret, db)
    if not creds:
        return _NOT_AUTH_MSG

    msg_id = args.get("message_id", "").strip()
    if not msg_id:
        return "message_id is required."

    svc = _service(creds)
    try:
        detail = svc.users().messages().get(
            userId="me", id=msg_id, format="full"
        ).execute()
    except HttpError as exc:
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            return (
                f"Gmail message '{msg_id}' was not found.\n"
                "This usually means the value is not a Gmail API message_id "
                "(for example an IMAP UID like '67885').\n"
                "Use gmail.search or gmail.list first, then pass the returned Gmail ID to gmail.read."
            )
        raise

    hdrs = _fmt_headers(detail.get("payload", {}).get("headers", []))
    body = _extract_body(detail.get("payload", {}))

    return (
        f"From: {hdrs.get('From', '?')}\n"
        f"To: {hdrs.get('To', '?')}\n"
        f"Subject: {hdrs.get('Subject', '(no subject)')}\n"
        f"Date: {hdrs.get('Date', '?')}\n\n"
        f"{body[:3000]}"
    )


async def gmail_send(args: Dict[str, Any], *, client_id, client_secret, db) -> str:
    """Send an email. Requires approval (RISK_RED)."""
    from .auth import get_credentials

    creds = get_credentials(client_id, client_secret, db)
    if not creds:
        return _NOT_AUTH_MSG

    to = args.get("to", "").strip()
    subject = args.get("subject", "").strip()
    body = args.get("body", "").strip()
    if not to or not subject or not body:
        return "to, subject, and body are all required."

    msg = email.mime.text.MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc = _service(creds)
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return f"Email sent to {to}."


async def gmail_search(args: Dict[str, Any], *, client_id, client_secret, db) -> str:
    """Search emails by Gmail query syntax."""
    args = dict(args)
    args["query"] = args.pop("query", "in:inbox")
    return await gmail_list(args, client_id=client_id, client_secret=client_secret, db=db)



def _extract_body(payload: dict, depth: int = 0) -> str:
    """Recursively extract plain-text body from MIME payload."""
    if depth > 5:
        return ""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        result = _extract_body(part, depth + 1)
        if result:
            return result

    if mime_type.startswith("text/") and body_data:
        return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")

    return "(no text body)"
