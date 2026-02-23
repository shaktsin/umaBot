from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from umabot.models import IncomingMessage
from umabot.policy import PolicyEngine, PendingConfirmation

if TYPE_CHECKING:
    from umabot.control_panel import ControlPanelManager


@dataclass
class ControlConfig:
    channel: str
    chat_id: Optional[str]
    connector: Optional[str] = None


@dataclass
class RoutedMessage:
    message: IncomingMessage
    kind: str  # control | external
    pending_confirmation: Optional[PendingConfirmation] = None


class MessageRouter:
    def __init__(
        self,
        policy: PolicyEngine,
        control: ControlConfig,
        control_panel: Optional["ControlPanelManager"] = None,
    ) -> None:
        self._policy = policy
        self._control = control
        self._control_panel = control_panel

    def route(self, message: IncomingMessage, kind_hint: Optional[str] = None) -> RoutedMessage:
        if kind_hint in {"control", "external"}:
            is_control = kind_hint == "control"
        else:
            is_control = self._is_control_message(message)
        pending = None
        if is_control:
            pending = self._policy.consume_confirmation(message.chat_id, message.text)
        kind = "control" if is_control else "external"
        return RoutedMessage(message=message, kind=kind, pending_confirmation=pending)

    def update_control(self, control: ControlConfig) -> None:
        self._control = control

    def _is_control_message(self, message: IncomingMessage) -> bool:
        """
        Check if message is from control panel.

        Uses new control_panel manager if available, falls back to old control config.
        """
        # Try new control panel first
        if self._control_panel and self._control_panel.config.enabled:
            return self._control_panel.is_control_message(
                channel=message.channel,
                chat_id=message.chat_id,
                connector=message.connector,
            )

        # DEPRECATED: Fall back to old control config for backward compatibility
        if not self._control.channel or not self._control.chat_id:
            return False
        return (
            message.channel == self._control.channel
            and str(message.chat_id) == str(self._control.chat_id)
        )
