"""Control panel manager — supports multiple simultaneous panels."""

from __future__ import annotations

import asyncio
import logging
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from umabot.gateway import Gateway
    from umabot.config.schema import ControlPanelConfig

logger = logging.getLogger("umabot.control_panel")


class ControlPanelManager:
    """Manages one or more owner control panel interfaces.

    All enabled panels receive every notification simultaneously.  The web panel
    is routed through the hub; messaging panels (telegram, discord) use their
    connector's send path.

    ``panels`` is the authoritative list.  It is built from:
      - ``config.control_panels``  (explicit list in config.yaml)
      - ``config.control_panel``   (legacy single-panel field, appended if enabled)
    """

    def __init__(self, config: "ControlPanelConfig", gateway: "Gateway") -> None:
        self.gateway = gateway
        # Build the unified panel list from both config sources
        self.panels: List[ControlPanelConfig] = _build_panel_list(config, gateway.config)
        # Legacy: expose primary config so existing gateway code that reads
        # self._control_panel.config still works without changes.
        self.config = config

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_notification(self, message: str) -> None:
        """Send *message* to every enabled panel."""
        tasks = [self._send_to_panel(panel, message) for panel in self.panels if panel.enabled]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_to_panel(self, panel: "ControlPanelConfig", message: str) -> None:
        try:
            if panel.ui_type == "web":
                await self.gateway.send_message("web", "admin", message, connector="web-panel")
            else:
                channel = _channel_for_panel(panel, self.gateway.config)
                if channel and panel.chat_id:
                    await self.gateway.send_message(
                        channel=channel,
                        chat_id=panel.chat_id,
                        text=message,
                        connector=panel.connector or "",
                    )
        except Exception as exc:
            logger.error("Failed to send to control panel (type=%s): %s", panel.ui_type, exc)

    # ------------------------------------------------------------------
    # Routing helpers
    # ------------------------------------------------------------------

    def is_control_message(self, channel: str, chat_id: str, connector: str = "") -> bool:
        """Return True if the message matches any enabled panel."""
        for panel in self.panels:
            if not panel.enabled:
                continue
            panel_channel = _channel_for_panel(panel, self.gateway.config)
            if channel != panel_channel:
                continue
            if chat_id != panel.chat_id:
                continue
            if panel.connector and connector and connector != panel.connector:
                continue
            return True
        return False

    async def request_confirmation(self, prompt: str, timeout: int = 300) -> bool:
        await self.send_notification(prompt)
        logger.warning("Confirmation request not fully implemented yet")
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_panel_list(
    primary: "ControlPanelConfig",
    config,
) -> "List[ControlPanelConfig]":
    """Merge primary + extra panels into a deduplicated list."""
    panels: list = []
    seen_types: set = set()

    def _add(panel: "ControlPanelConfig") -> None:
        key = (panel.ui_type, panel.chat_id or "", panel.connector or "")
        if key not in seen_types:
            seen_types.add(key)
            panels.append(panel)

    # Extra panels first (higher priority / more specific)
    for p in getattr(config, "control_panels", []):
        _add(p)

    # Legacy primary
    if primary and primary.enabled:
        _add(primary)

    return panels


def _channel_for_panel(panel: "ControlPanelConfig", config) -> str:
    """Resolve channel string for a messaging-type panel."""
    if panel.ui_type == "web":
        return "web"
    if not panel.connector:
        return panel.ui_type  # best-effort fallback
    for conn in getattr(config, "connectors", []):
        name = conn.get("name") if isinstance(conn, dict) else getattr(conn, "name", "")
        if name != panel.connector:
            continue
        conn_type = conn.get("type") if isinstance(conn, dict) else getattr(conn, "type", "")
        if "telegram" in conn_type:
            return "telegram"
        if "discord" in conn_type:
            return "discord"
        if "whatsapp" in conn_type:
            return "whatsapp"
    return panel.ui_type
