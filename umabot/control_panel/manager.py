"""Control panel manager for owner interaction."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from umabot.gateway import Gateway
    from umabot.config.schema import ControlPanelConfig

logger = logging.getLogger("umabot.control_panel")


class ControlPanelManager:
    """
    Manages the owner control panel interface.

    The control panel is the owner's private interface for:
    - Receiving notifications
    - Confirming sensitive actions
    - Managing the assistant

    This is separate from regular message connectors that handle external user messages.
    """

    def __init__(self, config: "ControlPanelConfig", gateway: "Gateway"):
        """
        Initialize control panel manager.

        Args:
            config: Control panel configuration
            gateway: Gateway instance for sending messages
        """
        self.config = config
        self.gateway = gateway
        self._channel = self._determine_channel()

    def _determine_channel(self) -> str:
        """Determine channel type from connector name."""
        if not self.config.connector:
            return ""

        # Find connector in gateway config
        for conn in self.gateway.config.connectors:
            if (isinstance(conn, dict) and conn.get("name") == self.config.connector) or \
               (hasattr(conn, "name") and conn.name == self.config.connector):
                # Extract channel from connector type
                conn_type = conn.get("type") if isinstance(conn, dict) else conn.type
                if "telegram" in conn_type:
                    return "telegram"
                elif "discord" in conn_type:
                    return "discord"
                elif "whatsapp" in conn_type:
                    return "whatsapp"

        logger.warning(f"Could not determine channel for connector: {self.config.connector}")
        return ""

    async def send_notification(self, message: str) -> None:
        """
        Send a notification to the owner via control panel.

        Args:
            message: Notification message to send
        """
        if not self.config.enabled:
            logger.debug("Control panel not enabled, notification not sent")
            return

        if not self.config.chat_id:
            logger.warning("Control panel chat_id not configured, cannot send notification")
            return

        try:
            await self.gateway.send_message(
                channel=self._channel,
                chat_id=self.config.chat_id,
                text=message,
                connector=self.config.connector,
            )
            logger.debug(f"Sent notification to control panel: {message[:50]}...")
        except Exception as exc:
            logger.error(f"Failed to send control panel notification: {exc}")

    async def request_confirmation(
        self, prompt: str, timeout: int = 300
    ) -> bool:
        """
        Request confirmation from owner for a sensitive action.

        Args:
            prompt: Confirmation prompt to show owner
            timeout: Timeout in seconds (default: 5 minutes)

        Returns:
            True if confirmed, False if denied or timeout
        """
        if not self.config.enabled:
            logger.warning("Control panel not enabled, cannot request confirmation")
            return False

        # Send confirmation request
        await self.send_notification(prompt)

        # Wait for response (implementation will depend on policy engine integration)
        # For now, this is a placeholder
        # In the full implementation, this would:
        # 1. Generate a confirmation token
        # 2. Store it in pending confirmations
        # 3. Wait for the user to respond with the token
        # 4. Return True/False based on response

        logger.warning("Confirmation request not fully implemented yet")
        return False

    def is_control_message(self, channel: str, chat_id: str, connector: str = "") -> bool:
        """
        Check if a message is from the control panel.

        Args:
            channel: Channel type (telegram, discord, etc.)
            chat_id: Chat ID
            connector: Optional connector name

        Returns:
            True if this is a control panel message
        """
        if not self.config.enabled:
            return False

        # Check channel and chat_id match
        if channel != self._channel:
            return False

        if chat_id != self.config.chat_id:
            return False

        # If connector is specified in config, also check it matches
        if self.config.connector and connector:
            return connector == self.config.connector

        return True
