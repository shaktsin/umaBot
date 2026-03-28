"""Gmail IMAP IDLE connector — proactive email notifications with no GCP dependency.

Flow:
  1. Connect to imap.gmail.com:993 over SSL.
  2. Authenticate via OAuth2 XOAUTH2 (reuses the token stored by google.authorize).
  3. SELECT the watched mailbox (default: INBOX).
  4. Enter IMAP IDLE — server pushes an EXISTS line when new mail arrives.
  5. On push: exit IDLE, fetch full message, forward to LLM via gateway.
  6. Re-enter IDLE. Re-IDLE every 25 min (Gmail terminates IDLE at 30 min).
  7. On any error or WS disconnect: reconnect with exponential backoff.

Config (in config.yaml connectors list):
  - name: gmail_imap
    type: gmail_imap
    mailbox: INBOX               # IMAP mailbox to watch (default: INBOX)
    # Notifications auto-route to the local control panel — no reply fields needed.

Prerequisites:
  1. Enable IMAP in Gmail settings → Forwarding and POP/IMAP → Enable IMAP.
  2. Authorise Google in UmaBot: run the google.authorize tool.
  3. Add the connector to config.yaml and restart.
"""

from __future__ import annotations

import argparse
import asyncio
import email
import email.header
import logging
import sys
import aiohttp

logger = logging.getLogger("umabot.connectors.gmail")

_IMAP_HOST = "imap.gmail.com"
_IMAP_PORT = 993
_IDLE_TIMEOUT = 25 * 60      # 25 min — Gmail terminates IDLE at 30 min
_RECONNECT_BASE = 5
_RECONNECT_MAX = 60
_CHANNEL = "gmail"
_MODE = "channel"


# ---------------------------------------------------------------------------
# IMAP helpers (sync — called via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _get_email_address(creds) -> str:
    from googleapiclient.discovery import build
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return svc.users().getProfile(userId="me").execute()["emailAddress"]


def _decode_header_value(raw: str) -> str:
    parts = email.header.decode_header(raw)
    return "".join(
        part.decode(enc or "utf-8") if isinstance(part, bytes) else part
        for part, enc in parts
    )


_MAX_BODY_CHARS = 4000   # truncate very long emails before sending to LLM


def _extract_body(msg) -> str:
    """Return the plaintext body of an email.message.Message, truncated if needed."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = part.get("Content-Disposition", "")
            if ct == "text/plain" and "attachment" not in cd:
                charset = part.get_content_charset() or "utf-8"
                try:
                    body = part.get_payload(decode=True).decode(charset, errors="replace")
                    break
                except Exception:
                    pass
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            body = msg.get_payload(decode=True).decode(charset, errors="replace")
        except Exception:
            pass
    body = body.strip()
    if len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS] + "\n… [truncated]"
    return body


def _format_email_for_llm(raw: bytes, uid: int) -> str:
    """Build the text that is forwarded to the LLM as the 'user' message."""
    msg = email.message_from_bytes(raw)
    subject = _decode_header_value(msg.get("Subject", "(no subject)"))
    from_ = _decode_header_value(msg.get("From", "?"))
    to_ = _decode_header_value(msg.get("To", "?"))
    date = msg.get("Date", "?")
    body = _extract_body(msg)

    return (
        f"[Incoming email — uid={uid}]\n"
        f"From:    {from_}\n"
        f"To:      {to_}\n"
        f"Date:    {date}\n"
        f"Subject: {subject}\n"
        f"\n{body or '(no body)'}\n"
        f"\n---\n"
        f"Summarize this email and tell me if it needs my attention. "
        f"If a reply is appropriate, draft one and ask me to confirm before sending."
    )


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

class GmailImapConnector:
    def __init__(
        self,
        *,
        name: str,
        mailbox: str,
        reply_connector: str,
        reply_chat_id: str,
        reply_channel: str,
        ws_url: str,
        ws_token: str,
        db,
        client_id: str,
        client_secret: str,
    ) -> None:
        self.name = name
        self.mailbox = mailbox
        self.reply_connector = reply_connector
        self.reply_chat_id = reply_chat_id
        self.reply_channel = reply_channel
        self.ws_url = ws_url
        self.ws_token = ws_token
        self.db = db
        self.client_id = client_id
        self.client_secret = client_secret
        self._stop = asyncio.Event()

    def _get_creds(self):
        from umabot.tools.google.auth import get_credentials
        creds = get_credentials(self.client_id, self.client_secret, self.db)
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        return creds

    # ------------------------------------------------------------------
    # Main loop — WS reconnect wrapper
    # ------------------------------------------------------------------

    async def run(self) -> None:
        backoff = _RECONNECT_BASE
        while not self._stop.is_set():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(self.ws_url) as ws:
                        await ws.send_json({
                            "type": "hello",
                            "token": self.ws_token,
                            "connector": self.name,
                            "channel": _CHANNEL,
                            "mode": _MODE,
                        })
                        msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
                        if msg.get("type") != "ready":
                            logger.error("Unexpected WS handshake response: %s", msg)
                            await asyncio.sleep(backoff)
                            continue

                        logger.info("Gmail IMAP connector connected to gateway connector=%s", self.name)
                        backoff = _RECONNECT_BASE
                        await self._imap_idle_loop(ws)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Gmail connector error: %s — reconnecting in %ds", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX)

    # ------------------------------------------------------------------
    # IMAP IDLE loop
    # ------------------------------------------------------------------

    async def _imap_idle_loop(self, ws) -> None:
        import aioimaplib

        creds = self._get_creds()
        if not creds:
            logger.error(
                "Gmail not authorised for connector=%s. Run google.authorize first.", self.name
            )
            await asyncio.sleep(60)
            return

        email_addr = await asyncio.to_thread(_get_email_address, creds)
        logger.info("Gmail IMAP connecting as %s mailbox=%s", email_addr, self.mailbox)

        imap = aioimaplib.IMAP4_SSL(host=_IMAP_HOST, port=_IMAP_PORT)
        await imap.wait_hello_from_server()

        token = creds.token.decode() if isinstance(creds.token, bytes) else creds.token
        typ, data = await imap.xoauth2(email_addr, token)
        if typ != "OK":
            logger.error("IMAP XOAUTH2 auth failed: %s", data)
            await imap.logout()
            return

        typ, data = await imap.select(self.mailbox)
        if typ != "OK":
            logger.error("IMAP SELECT %s failed: %s", self.mailbox, data)
            await imap.logout()
            return

        last_uid = self.db.get_gmail_imap_last_uid(self.name)

        # On first run (last_uid=0) snapshot the current max UID so we only
        # notify about emails that arrive *after* startup.
        if last_uid == 0:
            resp = await imap.uid_search("ALL")
            if resp.result == "OK":
                raw = resp.lines[0].decode() if resp.lines and resp.lines[0] else ""
                existing = [int(u) for u in raw.split() if u.isdigit()]
                if existing:
                    last_uid = max(existing)
                    self.db.set_gmail_imap_last_uid(self.name, last_uid)
                    logger.info("Gmail IMAP first-run snapshot connector=%s last_uid=%d", self.name, last_uid)

        logger.info("Gmail IMAP ready connector=%s last_uid=%d", self.name, last_uid)

        try:
            while not self._stop.is_set():
                idle_task = await imap.idle_start(timeout=_IDLE_TIMEOUT)

                try:
                    await asyncio.wait_for(imap.wait_server_push(), timeout=_IDLE_TIMEOUT)
                    logger.debug("Gmail IMAP push received connector=%s", self.name)
                except asyncio.TimeoutError:
                    pass  # 25-min keepalive re-IDLE

                imap.idle_done()  # synchronous — sends DONE, cancels waiter
                await asyncio.wait_for(asyncio.shield(idle_task), timeout=10)

                # Refresh token if needed
                creds = self._get_creds()
                if not creds:
                    logger.error("Gmail credentials lost — stopping IMAP loop")
                    break

                # Fetch UIDs newer than last seen (uid_search returns actual UIDs)
                resp = await imap.uid_search(f"{last_uid + 1}:*")
                if resp.result != "OK":
                    continue

                raw = resp.lines[0].decode() if resp.lines and resp.lines[0] else ""
                new_uids = [int(u) for u in raw.split() if u.isdigit() and int(u) > last_uid]

                for uid in new_uids:
                    preview = await self._fetch_email(imap, uid)
                    try:
                        await ws.send_json({
                            "type": "event",
                            "chat_id": f"gmail:{self.name}",
                            "user_id": f"gmail:{self.name}",
                            "text": preview,
                            "reply_connector": self.reply_connector,
                            "reply_chat_id": self.reply_chat_id,
                            "reply_channel": self.reply_channel,
                        })
                        logger.info("Forwarded Gmail notification uid=%d connector=%s", uid, self.name)
                    except Exception as exc:
                        logger.warning("Failed to send event to gateway: %s", exc)

                if new_uids:
                    last_uid = max(new_uids)
                    self.db.set_gmail_imap_last_uid(self.name, last_uid)

        finally:
            try:
                await imap.logout()
            except Exception:
                pass

    async def _fetch_email(self, imap, uid: int) -> str:
        """Fetch the full RFC 2822 message and format it for the LLM."""
        resp = await imap.uid("fetch", str(uid), "(BODY.PEEK[])")
        if resp.result != "OK" or not resp.lines:
            return f"[Incoming email — uid={uid}]\n(could not fetch body)\nSummarize: new email arrived."
        for part in resp.lines:
            if isinstance(part, bytes) and len(part) > 10:
                try:
                    return _format_email_for_llm(part, uid)
                except Exception:
                    pass
        return f"[Incoming email — uid={uid}]\n(could not parse body)\nSummarize: new email arrived."

    async def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="UmaBot Gmail IMAP connector")
    parser.add_argument("--connector", required=True, help="Connector name from config")
    parser.add_argument("--config", default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    from umabot.config import load_config
    from umabot.storage import Database

    cfg, _ = load_config(config_path=args.config)

    conn_cfg = next(
        (c for c in cfg.connectors
         if (getattr(c, "name", None) or c.get("name", "")) == args.connector),
        None,
    )
    if conn_cfg is None:
        logger.error("Connector '%s' not found in config", args.connector)
        sys.exit(1)

    def _f(field, default=""):
        return getattr(conn_cfg, field, None) or (
            conn_cfg.get(field) if isinstance(conn_cfg, dict) else None
        ) or default

    google_cfg = getattr(cfg, "integrations", None)
    google_cfg = getattr(google_cfg, "google", None) if google_cfg else None
    client_id = (getattr(google_cfg, "client_id", "") or "") if google_cfg else ""
    client_secret = (getattr(google_cfg, "client_secret", "") or "") if google_cfg else ""

    if not client_id or not client_secret:
        logger.error("Google client_id / client_secret not configured in integrations.google")
        sys.exit(1)

    db = Database(cfg.storage.db_path)
    ws_url = f"ws://{cfg.runtime.ws_host}:{cfg.runtime.ws_port}/ws"

    connector = GmailImapConnector(
        name=args.connector,
        mailbox=_f("mailbox", "INBOX"),
        reply_connector=_f("reply_connector"),
        reply_chat_id=_f("reply_chat_id"),
        reply_channel=_f("reply_channel", "telegram"),
        ws_url=ws_url,
        ws_token=cfg.runtime.ws_token or "",
        db=db,
        client_id=client_id,
        client_secret=client_secret,
    )

    async def _run():
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()
        import signal as _signal
        for sig in (_signal.SIGINT, _signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass
        task = asyncio.create_task(connector.run())
        await stop_event.wait()
        await connector.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


if __name__ == "__main__":
    main()
