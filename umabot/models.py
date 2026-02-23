"""Shared data models for UmaBot."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IncomingMessage:
    """Message received from any connector/channel.

    This model is used throughout UmaBot to represent incoming messages
    from various platforms (Telegram, Discord, WhatsApp, etc.).
    """
    channel: str  # Platform type (telegram, discord, whatsapp)
    chat_id: str  # Chat/conversation identifier
    user_id: str  # User identifier
    text: str     # Message text content
    connector: str = ""  # Connector name that sent this message
