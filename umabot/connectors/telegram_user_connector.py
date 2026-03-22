"""Telegram User Account connector using Telethon."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Optional

from aiohttp import ClientSession, WSMsgType
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from umabot.connectors.telegram_format import markdown_to_telegram_html, split_message
import qrcode

from umabot.config import load_config
from umabot.connectors.base import BaseConnector, ConnectorStatus
from umabot.storage import Database

logger = logging.getLogger("umabot.connectors.telegram_user")


class TelegramUserConnector(BaseConnector):
    """
    Telegram User Account connector using Telethon.

    Connects as a user account (not a bot) and can read all user chats and channels.
    Requires Telegram API credentials (api_id, api_hash) and phone number for initial login.
    """

    def __init__(
        self,
        *,
        name: str,
        api_id: int,
        api_hash: str,
        session_string: Optional[str],
        phone: Optional[str],
        ws_url: str,
        ws_token: str,
        allow_login: bool,
        db_path: str,
    ) -> None:
        super().__init__(name=name, ws_url=ws_url, ws_token=ws_token)
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string
        self.phone = phone
        self.allow_login = allow_login
        self.db_path = db_path
        self._ws = None
        self._stop = asyncio.Event()
        self._connected = False
        self._last_message_at: Optional[str] = None

    async def run(self) -> None:
        """Main connector loop."""
        logger.info(f"Starting Telegram User connector: {self.name}")
        async with ClientSession() as http_session:
            while not self._stop.is_set():
                client = None
                try:
                    async with http_session.ws_connect(self.ws_url) as ws:
                        self._ws = ws

                        # Send hello message to gateway
                        await ws.send_json(
                            {
                                "type": "hello",
                                "token": self.ws_token,
                                "connector": self.name,
                                "channel": "telegram",
                                "mode": "channel",
                            }
                        )

                        # Wait for ready confirmation
                        ready = await ws.receive_json()
                        if ready.get("type") != "ready":
                            raise RuntimeError("WebSocket handshake failed")

                        logger.info(f"WebSocket connected for connector: {self.name}")

                        # Create Telethon client
                        session = StringSession(self.session_string)
                        client = TelegramClient(session, self.api_id, self.api_hash)
                        await client.connect()

                        # Check authorization
                        if not await client.is_user_authorized():
                            if not self.allow_login:
                                raise SystemExit(
                                    "Telegram user session not authorized. Run with --login to complete first-time auth."
                                )

                            # QR code login (no phone required!)
                            logger.info("Starting QR code login - scan the QR code below with Telegram mobile app")
                            qr_login = await client.qr_login()
                            _print_qr(qr_login.url)
                            print("\n📱 Open Telegram on your phone → Settings → Devices → Link Desktop Device")
                            print("   Scan the QR code above to log in\n")
                            await qr_login.wait()
                            logger.info(f"✓ QR login successful for connector: {self.name}")

                        # Persist session for next run
                        _persist_session(self.db_path, self.name, client.session.save())

                        # Register event handler for new messages
                        client.add_event_handler(
                            self._on_new_message, events.NewMessage(incoming=True)
                        )

                        self._connected = True
                        logger.info(f"Telegram User connector {self.name} ready")

                        # Run recv loop (outbound sends) alongside Telethon client
                        recv_task = asyncio.create_task(self._recv_loop(ws, client))
                        try:
                            await client.run_until_disconnected()
                        finally:
                            recv_task.cancel()
                except asyncio.CancelledError:
                    raise
                except SystemExit:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Connector %s failed to connect/run (%s). Retrying in 2s...",
                        self.name,
                        exc,
                    )
                    await asyncio.sleep(2)
                finally:
                    self._connected = False
                    self._ws = None
                    if client is not None and client.is_connected():
                        await client.disconnect()

    async def _on_new_message(self, event) -> None:
        """Handle new message from Telegram."""
        if not self._ws:
            return

        chat_id = str(event.chat_id or "")
        sender_id = str(event.sender_id or "")
        text = event.raw_text or ""

        logger.debug("Telegram user message received text_len=%s", len(text))

        # Forward to gateway
        await self._ws.send_json(
            {
                "type": "event",
                "chat_id": chat_id,
                "user_id": sender_id,
                "text": text,
            }
        )

        from datetime import datetime

        self._last_message_at = datetime.utcnow().isoformat()

    async def _recv_loop(self, ws, client: TelegramClient) -> None:
        """Receive send commands from gateway and send via Telethon."""
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = msg.json()
                    if data.get("type") == "send":
                        chat_id = data.get("chat_id", "")
                        text = data.get("text", "")
                        await self._send_message(client, chat_id, text)
                elif msg.type in {WSMsgType.CLOSE, WSMsgType.ERROR}:
                    break
        except asyncio.CancelledError:
            pass

    async def _send_message(self, client: TelegramClient, chat_id: str, text: str) -> None:
        """Send formatted message via Telethon."""
        if not chat_id:
            return

        html_text = markdown_to_telegram_html(text)
        chunks = split_message(html_text)

        for chunk in chunks:
            try:
                await client.send_message(int(chat_id), chunk, parse_mode="html")
                logger.debug("Sent Telethon message")
            except Exception:
                # Fallback: send without formatting
                try:
                    await client.send_message(int(chat_id), chunk)
                    logger.debug("Sent Telethon message (plain fallback)")
                except Exception as exc:
                    logger.error("Failed to send Telethon message: %s", exc)

    async def health_check(self) -> ConnectorStatus:
        """Return current health status."""
        return ConnectorStatus(
            name=self.name,
            channel="telegram",
            status="connected" if self._connected else "disconnected",
            last_message_at=self._last_message_at,
            error=None,
        )


def _persist_session(db_path: str, connector: str, session_string: str) -> None:
    """Persist Telegram session to database."""
    db = Database(db_path)
    db.upsert_connector_session(connector, "telegram_user", session_string.encode("utf-8"))
    db.close()


def _load_session(db_path: str, connector: str) -> Optional[str]:
    """Load Telegram session from database."""
    db = Database(db_path)
    raw = db.get_connector_session(connector, "telegram_user")
    db.close()
    if not raw:
        return None
    return raw.decode("utf-8")


def _default_ws_url(cfg) -> str:
    """Build default WebSocket URL from config."""
    host = cfg.runtime.ws_host
    port = cfg.runtime.ws_port
    return f"ws://{host}:{port}/ws"


def _debug_secrets_enabled() -> bool:
    """Check if debug mode for secrets is enabled."""
    return os.environ.get("UMABOT_DEBUG_SECRETS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def _print_qr(url: str) -> None:
    """Print QR code to terminal."""
    qr = qrcode.QRCode()
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    logger.info("Scan the QR code above in Telegram (Settings > Devices > Link Desktop Device)")


def _find_connector(cfg, connector_name: str):
    """Find connector configuration by name."""
    for conn in cfg.connectors:
        # Support both dict and object formats
        if isinstance(conn, dict):
            if conn.get("name") == connector_name:
                return conn
        else:
            if conn.name == connector_name:
                return conn
    return None


def main() -> None:
    """CLI entry point for Telegram User connector."""
    parser = argparse.ArgumentParser(
        description="Telegram User Account connector for UMA BOT"
    )
    parser.add_argument("--config", dest="config", default=None)
    parser.add_argument("--connector", dest="connector", required=True, help="Connector name from config")
    parser.add_argument("--api-id", dest="api_id", default=None)
    parser.add_argument("--api-hash", dest="api_hash", default=None)
    parser.add_argument("--phone", dest="phone", default=None)
    parser.add_argument("--ws-url", dest="ws_url", default=None)
    parser.add_argument("--ws-token", dest="ws_token", default=None)
    parser.add_argument("--log-level", dest="log_level", default=None)
    parser.add_argument(
        "--login", dest="login", action="store_true", help="Allow interactive login"
    )
    args = parser.parse_args()

    log_level = (args.log_level or "INFO").upper()
    logging.basicConfig(
        level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # Load configuration
    cfg, _ = load_config(config_path=args.config)

    # Find connector config
    connector_cfg = _find_connector(cfg, args.connector)
    if not connector_cfg:
        raise SystemExit(f"Connector '{args.connector}' not found in configuration")

    # Extract credentials from connector config
    if isinstance(connector_cfg, dict):
        api_id = args.api_id or connector_cfg.get("api_id")
        api_hash = args.api_hash or connector_cfg.get("api_hash")
        phone = args.phone or connector_cfg.get("phone")
    else:
        api_id = args.api_id or connector_cfg.api_id
        api_hash = args.api_hash or connector_cfg.api_hash
        phone = args.phone or connector_cfg.phone

    logger.info(
        "Resolved Telegram user credentials api_id_configured=%s api_hash_configured=%s phone_configured=%s",
        bool(api_id),
        bool(api_hash),
        bool(phone),
    )

    if not api_id or not api_hash:
        raise SystemExit("Telegram user connector requires api_id and api_hash")

    # Get WebSocket details
    ws_token = args.ws_token or cfg.runtime.ws_token
    if not ws_token:
        raise SystemExit("WebSocket token not configured (UMABOT_WS_TOKEN)")

    ws_url = args.ws_url or _default_ws_url(cfg)

    # Load saved session if available
    session_string = _load_session(cfg.storage.db_path, args.connector)

    # Create and run connector
    connector = TelegramUserConnector(
        name=args.connector,
        api_id=int(api_id),
        api_hash=str(api_hash),
        session_string=session_string,
        phone=phone,
        ws_url=ws_url,
        ws_token=ws_token,
        allow_login=args.login,
        db_path=cfg.storage.db_path,
    )

    try:
        asyncio.run(connector.run())
    except KeyboardInterrupt:
        logger.info("Connector stopped by user")
    except Exception as exc:
        logger.error(f"Connector failed: {exc}", exc_info=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
