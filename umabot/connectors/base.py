"""Base connector interface for all message source connectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class ConnectorStatus:
    """Status information for a connector."""

    name: str
    channel: str  # telegram | discord | whatsapp
    status: str  # connecting | connected | disconnected | error
    last_message_at: Optional[str] = None
    error: Optional[str] = None


class BaseConnector(ABC):
    """
    Base class for all connectors.

    Connectors are message source workers that run as separate processes
    and communicate with the gateway via WebSocket.
    """

    def __init__(self, name: str, ws_url: str, ws_token: str):
        """
        Initialize connector.

        Args:
            name: Unique connector name
            ws_url: WebSocket URL to gateway
            ws_token: Authentication token for WebSocket
        """
        self.name = name
        self.ws_url = ws_url
        self.ws_token = ws_token

    @abstractmethod
    async def run(self) -> None:
        """
        Main connector loop.

        Should:
        1. Connect to message source (Telegram, Discord, etc.)
        2. Connect to gateway via WebSocket
        3. Forward messages between source and gateway
        4. Handle reconnection and error cases
        """
        pass

    @abstractmethod
    async def health_check(self) -> ConnectorStatus:
        """
        Return current health status.

        Returns:
            ConnectorStatus with current state
        """
        pass
