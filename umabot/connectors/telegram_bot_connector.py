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
from urllib.error import HTTPError

from aiohttp import ClientSession, WSMsgType

from umabot.connectors.telegram_format import markdown_to_telegram_html, split_message
from umabot.connectors.telegram_resilience import (
    ChatCircuitBreakers,
    TelegramRateLimiter,
    backoff_delay,
)
from umabot.config import load_config
from umabot.connectors.base import BaseConnector, ConnectorStatus

logger = logging.getLogger("umabot.connectors.telegram_bot")

# Max send attempts per message chunk before giving up
_SEND_MAX_RETRIES = 3


class TelegramBotConnector(BaseConnector):
    """
    Telegram Bot API connector.

    Polls Telegram Bot API for updates and forwards messages to gateway via WebSocket.

    Resilience features:
      - Per-chat + global rate limiting (TelegramRateLimiter)
      - Retry with backoff on 429 / send failure
      - Per-chat circuit breaker (stops hammering a broken chat)
      - Exponential backoff on reconnect
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

        # Resilience
        self._rate_limiter = TelegramRateLimiter()
        self._circuit_breakers = ChatCircuitBreakers(
            failure_threshold=5, cooldown_seconds=300.0
        )

    async def run(self) -> None:
        """Main connector loop with exponential backoff on reconnect."""
        logger.info("Starting Telegram Bot connector: %s", self.name)
        attempt = 0
        async with ClientSession() as session:
            while not self._stop.is_set():
                try:
                    async with session.ws_connect(self.ws_url) as ws:
                        await ws.send_json(
                            {
                                "type": "hello",
                                "token": self.ws_token,
                                "connector": self.name,
                                "channel": "telegram",
                                "mode": "channel",
                            }
                        )
                        ready = await ws.receive_json()
                        if ready.get("type") != "ready":
                            raise RuntimeError("WebSocket handshake failed")

                        self._connected = True
                        attempt = 0  # reset backoff on successful connect
                        logger.info("Telegram Bot connector %s connected to gateway", self.name)

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
                    delay = backoff_delay(attempt)
                    attempt += 1
                    logger.warning(
                        "Connector %s failed (%s). Reconnecting in %.1fs (attempt %d)...",
                        self.name, exc, delay, attempt,
                    )
                    await asyncio.sleep(delay)
                finally:
                    self._connected = False

    async def _poll_loop(self, ws) -> None:
        """Poll Telegram Bot API for updates."""
        poll_attempt = 0
        while not self._stop.is_set():
            params = {"timeout": 20}
            if self._offset is not None:
                params["offset"] = self._offset

            try:
                data = await asyncio.to_thread(self._get, "getUpdates", params)
                poll_attempt = 0  # reset on success
            except Exception as exc:
                delay = backoff_delay(poll_attempt)
                poll_attempt += 1
                logger.warning("Telegram polling error (%s). Retrying in %.1fs...", exc, delay)
                await asyncio.sleep(delay)
                continue

            from datetime import datetime

            for update in data.get("result", []) or []:
                self._offset = update.get("update_id", 0) + 1

                # Handle inline keyboard button presses (approval flow)
                callback = update.get("callback_query")
                if callback:
                    await self._handle_callback_query(ws, callback)
                    continue

                message = update.get("message") or update.get("edited_message")
                if not message or "text" not in message:
                    continue

                chat_id = str(message["chat"]["id"])
                user_id = str(message.get("from", {}).get("id", ""))
                text = message.get("text", "")

                await ws.send_json(
                    {
                        "type": "event",
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "text": text,
                    }
                )
                self._last_message_at = datetime.utcnow().isoformat()
                logger.debug("Forwarded Telegram message to gateway")

    async def _handle_callback_query(self, ws, callback: Dict[str, Any]) -> None:
        """Handle an inline keyboard button press for tool approval."""
        callback_id = callback.get("id", "")
        chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
        user_id = str(callback.get("from", {}).get("id", ""))
        data = callback.get("data", "")

        if not data.startswith("confirm:"):
            return

        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        _, verdict, token = parts
        verdict = verdict.upper()

        # Acknowledge the button press (removes loading spinner)
        try:
            await asyncio.to_thread(
                self._post, "answerCallbackQuery", {"callback_query_id": callback_id}
            )
        except Exception as exc:
            logger.warning("answerCallbackQuery failed: %s", exc)

        await ws.send_json(
            {
                "type": "event",
                "chat_id": chat_id,
                "user_id": user_id,
                "text": f"{verdict} {token}",
            }
        )
        logger.debug("Forwarded callback approval chat_id=%s verdict=%s", chat_id, verdict)

    async def _recv_loop(self, ws) -> None:
        """Receive send commands from gateway."""
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                msg_type = data.get("type")
                chat_id = data.get("chat_id", "")
                if msg_type == "send":
                    await self._send_message(chat_id, data.get("text", ""))
                elif msg_type == "confirm_request":
                    await self._send_confirmation_request(
                        chat_id,
                        tool_name=data.get("tool_name", "unknown"),
                        args_preview=data.get("args_preview", ""),
                        token=data.get("token", ""),
                    )
            elif msg.type in {WSMsgType.CLOSE, WSMsgType.ERROR}:
                logger.warning("WebSocket closed or error")
                break

    async def _send_message(self, chat_id: str, text: str) -> None:
        """Send message with rate limiting, retry on 429, and circuit breaker."""
        if not chat_id:
            return

        cb = self._circuit_breakers.get(chat_id)
        if cb.is_open():
            logger.warning("Circuit open for chat_id=%s — dropping message", chat_id)
            return

        html_text = markdown_to_telegram_html(text)
        chunks = split_message(html_text)

        for chunk in chunks:
            await self._send_chunk(chat_id, chunk, cb)

    async def _send_chunk(self, chat_id: str, chunk: str, cb) -> None:
        """Send one chunk with rate limiting and retry on 429."""
        for attempt in range(_SEND_MAX_RETRIES):
            await self._rate_limiter.acquire(chat_id)
            try:
                await asyncio.to_thread(
                    self._post,
                    "sendMessage",
                    {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"},
                )
                cb.record_success()
                logger.debug("Sent Telegram message chat_id=%s", chat_id)
                return
            except HTTPError as exc:
                retry_after = _parse_retry_after(exc)
                if retry_after is not None:
                    logger.warning(
                        "Telegram 429 for chat_id=%s — waiting %.0fs (attempt %d/%d)",
                        chat_id, retry_after, attempt + 1, _SEND_MAX_RETRIES,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                # Non-429 HTTP error — try plain text fallback once
                if attempt == 0:
                    try:
                        await asyncio.to_thread(
                            self._post, "sendMessage", {"chat_id": chat_id, "text": chunk}
                        )
                        cb.record_success()
                        logger.debug("Sent Telegram message (plain fallback) chat_id=%s", chat_id)
                        return
                    except Exception as fallback_exc:
                        logger.warning("Plain fallback also failed: %s", fallback_exc)
                cb.record_failure()
                logger.error("Failed to send to chat_id=%s: %s", chat_id, exc)
                return
            except Exception as exc:
                cb.record_failure()
                logger.error("Failed to send to chat_id=%s: %s", chat_id, exc)
                return

        cb.record_failure()
        logger.error("Gave up sending to chat_id=%s after %d attempts", chat_id, _SEND_MAX_RETRIES)

    async def _send_confirmation_request(
        self, chat_id: str, tool_name: str, args_preview: str, token: str
    ) -> None:
        """Send a tool approval request with Approve/Deny inline keyboard buttons."""
        if not chat_id:
            return

        cb = self._circuit_breakers.get(chat_id)
        if cb.is_open():
            logger.warning("Circuit open for chat_id=%s — dropping confirm_request", chat_id)
            return

        text = f"⚠️ <b>Approval required</b>\nTool: <code>{tool_name}</code>"
        if args_preview:
            preview = args_preview[:300] + ("..." if len(args_preview) > 300 else "")
            text += f"\n\nArguments:\n<pre>{preview}</pre>"

        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"confirm:YES:{token}"},
                    {"text": "❌ Deny",    "callback_data": f"confirm:NO:{token}"},
                ]
            ]
        }

        await self._rate_limiter.acquire(chat_id)
        try:
            await asyncio.to_thread(
                self._post,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(keyboard),
                },
            )
            cb.record_success()
            logger.debug("Sent confirmation request with inline keyboard chat_id=%s", chat_id)
        except Exception as exc:
            cb.record_failure()
            logger.error("Failed to send confirmation request: %s", exc)
            # Fallback: plain text without buttons
            await self._send_message(
                chat_id,
                f"⚠️ Approval required: {tool_name}\n\nReply YES to approve or NO to deny.",
            )

    def _get(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.token}/{method}?{urlencode(params)}"
        req = Request(url, headers={"User-Agent": "umabot/0.1"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def health_check(self) -> ConnectorStatus:
        return ConnectorStatus(
            name=self.name,
            channel="telegram",
            status="connected" if self._connected else "disconnected",
            last_message_at=self._last_message_at,
            error=None,
        )


def _parse_retry_after(exc: HTTPError) -> Optional[float]:
    """Extract retry_after seconds from a Telegram 429 HTTPError body.

    Telegram returns: {"ok": false, "error_code": 429,
                       "parameters": {"retry_after": 30}}
    """
    if exc.code != 429:
        return None
    try:
        body = exc.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        return float(data["parameters"]["retry_after"])
    except Exception:
        return 30.0  # safe default


def _default_ws_url(cfg) -> str:
    host = cfg.runtime.ws_host
    port = cfg.runtime.ws_port
    return f"ws://{host}:{port}/ws"


def _find_connector(cfg, connector_name: str):
    for conn in cfg.connectors:
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
    parser = argparse.ArgumentParser(description="Telegram Bot connector for UMA BOT")
    parser.add_argument("--config", dest="config", default=None)
    parser.add_argument("--connector", dest="connector", required=True)
    parser.add_argument("--token", dest="token", default=None)
    parser.add_argument("--ws-url", dest="ws_url", default=None)
    parser.add_argument("--ws-token", dest="ws_token", default=None)
    parser.add_argument("--log-level", dest="log_level", default=None)
    args = parser.parse_args()

    log_level = (args.log_level or "INFO").upper()
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    cfg, _ = load_config(config_path=args.config)
    connector_cfg = _find_connector(cfg, args.connector)
    if not connector_cfg:
        raise SystemExit(f"Connector '{args.connector}' not found in configuration")

    if isinstance(connector_cfg, dict):
        token = args.token or connector_cfg.get("token")
    else:
        token = args.token or connector_cfg.token

    if not token:
        token = _connector_env_token(args.connector) or getattr(cfg.telegram, "token", None)

    if not token:
        raise SystemExit(f"No token configured for connector '{args.connector}'.")

    ws_token = args.ws_token or cfg.runtime.ws_token
    if not ws_token:
        raise SystemExit("WebSocket token not configured (UMABOT_WS_TOKEN)")

    ws_url = args.ws_url or _default_ws_url(cfg)

    connector = TelegramBotConnector(
        name=args.connector, token=token, ws_url=ws_url, ws_token=ws_token
    )

    try:
        asyncio.run(connector.run())
    except KeyboardInterrupt:
        logger.info("Connector stopped by user")
    except Exception as exc:
        logger.error("Connector failed: %s", exc, exc_info=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
