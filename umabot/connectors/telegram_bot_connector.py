"""Telegram Bot connector for receiving messages via Bot API."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from aiohttp import ClientSession, WSMsgType

from umabot.connectors.telegram_format import markdown_to_telegram_html, split_message

from umabot.config import load_config
from umabot.connectors.base import BaseConnector, ConnectorStatus

logger = logging.getLogger("umabot.connectors.telegram_bot")


class TelegramBotConnector(BaseConnector):
    """
    Telegram Bot API connector.

    Polls Telegram Bot API for updates and forwards messages to gateway via WebSocket.
    """

    def __init__(
        self,
        *,
        name: str,
        token: str,
        ws_url: str,
        ws_token: str,
    ) -> None:
        super().__init__(name=name, ws_url=ws_url, ws_token=ws_token)
        self.token = token
        self._offset: Optional[int] = None
        self._stop = asyncio.Event()
        self._connected = False
        self._last_message_at: Optional[str] = None

    async def run(self) -> None:
        """Main connector loop."""
        logger.info(f"Starting Telegram Bot connector: {self.name}")
        async with ClientSession() as session:
            while not self._stop.is_set():
                try:
                    async with session.ws_connect(self.ws_url) as ws:
                        # Send hello message to gateway
                        await ws.send_json(
                            {
                                "type": "hello",
                                "token": self.ws_token,
                                "connector": self.name,
                                "channel": "telegram",
                                "mode": "channel",  # Regular message connector
                            }
                        )

                        # Wait for ready confirmation
                        ready = await ws.receive_json()
                        if ready.get("type") != "ready":
                            raise RuntimeError("WebSocket handshake failed")

                        self._connected = True
                        logger.info(f"Telegram Bot connector {self.name} connected to gateway")

                        # Run polling and receiving loops concurrently
                        poll_task = asyncio.create_task(self._poll_loop(ws))
                        recv_task = asyncio.create_task(self._recv_loop(ws))

                        done, pending = await asyncio.wait(
                            [poll_task, recv_task], return_when=asyncio.FIRST_COMPLETED
                        )

                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        await asyncio.gather(*done, return_exceptions=True)
                except asyncio.CancelledError:
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

    async def _poll_loop(self, ws) -> None:
        """Poll Telegram Bot API for updates."""
        while not self._stop.is_set():
            params = {"timeout": 20}
            if self._offset is not None:
                params["offset"] = self._offset

            try:
                data = await asyncio.to_thread(self._get, "getUpdates", params)
            except Exception as exc:
                logger.warning(f"Telegram polling error: {exc}")
                await asyncio.sleep(2)
                continue

            for update in data.get("result", []) or []:
                self._offset = update.get("update_id", 0) + 1
                message = update.get("message") or update.get("edited_message")

                if not message or "text" not in message:
                    continue

                chat_id = str(message["chat"]["id"])
                user_id = str(message.get("from", {}).get("id", ""))
                text = message.get("text", "")

                # Forward to gateway
                await ws.send_json(
                    {
                        "type": "event",
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "text": text,
                    }
                )

                from datetime import datetime

                self._last_message_at = datetime.utcnow().isoformat()
                logger.debug("Forwarded Telegram message to gateway")

    async def _recv_loop(self, ws) -> None:
        """Receive send commands from gateway."""
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "send":
                    chat_id = data.get("chat_id", "")
                    text = data.get("text", "")
                    await self._send_message(chat_id, text)
            elif msg.type in {WSMsgType.CLOSE, WSMsgType.ERROR}:
                logger.warning("WebSocket closed or error")
                break

    async def _send_message(self, chat_id: str, text: str) -> None:
        """Send message to Telegram chat with formatting."""
        if not chat_id:
            return

        html_text = markdown_to_telegram_html(text)
        chunks = split_message(html_text)

        for chunk in chunks:
            payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
            try:
                await asyncio.to_thread(self._post, "sendMessage", payload)
                logger.debug("Sent Telegram message")
            except Exception:
                # Fallback: send without parse_mode in case HTML is malformed
                fallback_payload = {"chat_id": chat_id, "text": chunk}
                try:
                    await asyncio.to_thread(self._post, "sendMessage", fallback_payload)
                    logger.debug("Sent Telegram message (plain fallback)")
                except Exception as exc:
                    logger.error("Failed to send Telegram message: %s", exc)

    def _get(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Make GET request to Telegram Bot API."""
        url = f"https://api.telegram.org/bot{self.token}/{method}?{urlencode(params)}"
        req = Request(url, headers={"User-Agent": "umabot/0.1"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Make POST request to Telegram Bot API."""
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def health_check(self) -> ConnectorStatus:
        """Return current health status."""
        return ConnectorStatus(
            name=self.name,
            channel="telegram",
            status="connected" if self._connected else "disconnected",
            last_message_at=self._last_message_at,
            error=None,
        )


def _default_ws_url(cfg) -> str:
    """Build default WebSocket URL from config."""
    host = cfg.runtime.ws_host
    port = cfg.runtime.ws_port
    return f"ws://{host}:{port}/ws"


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


def _connector_env_token(connector_name: str) -> Optional[str]:
    normalized = "".join(
        ch if ch.isalnum() else "_"
        for ch in connector_name.strip().upper()
    )
    candidates = [
        f"UMABOT_CONNECTOR_{normalized}_TOKEN",
        "UMABOT_TELEGRAM_BOT_TOKEN",
        "UMABOT_TELEGRAM_TOKEN",
    ]
    for key in candidates:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def main() -> None:
    """CLI entry point for Telegram Bot connector."""
    parser = argparse.ArgumentParser(
        description="Telegram Bot connector for UMA BOT"
    )
    parser.add_argument("--config", dest="config", default=None)
    parser.add_argument("--connector", dest="connector", required=True, help="Connector name from config")
    parser.add_argument("--token", dest="token", default=None, help="Override Telegram bot token")
    parser.add_argument("--ws-url", dest="ws_url", default=None)
    parser.add_argument("--ws-token", dest="ws_token", default=None)
    parser.add_argument("--log-level", dest="log_level", default=None)
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

    # Get token (support both dict and object formats)
    if isinstance(connector_cfg, dict):
        token = args.token or connector_cfg.get("token")
    else:
        token = args.token or connector_cfg.token

    # Fallback to connector/env/keychain-loaded token
    if not token:
        token = _connector_env_token(args.connector) or getattr(cfg.telegram, "token", None)

    if not token:
        raise SystemExit(
            "No token configured for connector "
            f"'{args.connector}'. Set connector.token in config.yaml or export "
            f"UMABOT_CONNECTOR_{''.join(ch if ch.isalnum() else '_' for ch in args.connector.strip().upper())}_TOKEN."
        )

    # Get WebSocket details
    ws_token = args.ws_token or cfg.runtime.ws_token
    if not ws_token:
        raise SystemExit("WebSocket token not configured (UMABOT_WS_TOKEN)")

    ws_url = args.ws_url or _default_ws_url(cfg)

    # Create and run connector
    connector = TelegramBotConnector(
        name=args.connector,
        token=token,
        ws_url=ws_url,
        ws_token=ws_token,
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
