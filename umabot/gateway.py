from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Dict, Optional

from umabot.config import load_config, parse_override_args
from umabot.control_panel import ControlPanelManager
from umabot.models import IncomingMessage
from umabot.policy import PolicyEngine
from umabot.router import ControlConfig, MessageRouter
from umabot.scheduler import TaskScheduler
from umabot.skills import SkillRegistry
from umabot.skills.runtime import SkillRuntime
from umabot.storage import Database, Queue
from umabot.tools import ToolRegistry, UnifiedToolRegistry, register_builtin_tools, register_skill_tools
from umabot.worker import Worker
from umabot.ws import ChannelHub, WebsocketGateway

logger = logging.getLogger("umabot.gateway")


class Gateway:
    def __init__(
        self,
        *,
        config,
        config_path: str,
        overrides: Optional[Dict[str, str]] = None,
        db: Database,
        queue: Queue,
        tool_registry: ToolRegistry,
        policy: PolicyEngine,
        skill_registry: SkillRegistry,
        unified_registry: UnifiedToolRegistry,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.overrides = overrides or {}
        self.db = db
        self.queue = queue
        self.tool_registry = tool_registry
        self.policy = policy
        self.skill_registry = skill_registry
        self.unified_registry = unified_registry

        # Control panel manager (new architecture)
        self._control_panel = ControlPanelManager(config.control_panel, self)

        # DEPRECATED: Keep for backward compatibility
        self.control_channel = (config.runtime.control_channel or "").strip()
        self.control_chat_id = config.runtime.control_chat_id
        self.control_connector = config.runtime.control_connector

        self._router = MessageRouter(
            policy=policy,
            control=ControlConfig(
                channel=self.control_channel,
                chat_id=str(self.control_chat_id) if self.control_chat_id else None,
                connector=self.control_connector,
            ),
            control_panel=self._control_panel,
        )
        self._hub = ChannelHub(on_status=self._on_connector_status)
        self._ws_gateway: Optional[WebsocketGateway] = None
        self._worker = Worker(
            config=config,
            db=db,
            queue=queue,
            tool_registry=tool_registry,
            policy=policy,
            skill_registry=skill_registry,
            unified_registry=unified_registry,
            send_message=self.send_message,
            send_control_message=self.send_control_message,
        )
        self._scheduler = TaskScheduler(db=db, queue=queue)

    async def start(self) -> None:
        await self._start_ws_gateway()
        await self._worker.start()
        await self._scheduler.start()
        logger.info("Gateway started")

    async def stop(self) -> None:
        await self._scheduler.stop()
        await self._worker.stop()
        await self._stop_ws_gateway()
        logger.info("Gateway stopped")

    async def reload(self) -> None:
        logger.info("Reloading configuration")
        config, config_path, tool_registry, policy, skill_registry, unified_registry = _reload_runtime(
            self.config_path, overrides=self.overrides
        )
        self.config = config
        self.config_path = config_path
        self.tool_registry = tool_registry
        self.policy = policy
        self.skill_registry = skill_registry
        self.unified_registry = unified_registry
        self.control_channel = (config.runtime.control_channel or "").strip()
        self.control_chat_id = config.runtime.control_chat_id
        self.control_connector = config.runtime.control_connector
        self._router.update_control(
            ControlConfig(
                channel=self.control_channel,
                chat_id=str(self.control_chat_id) if self.control_chat_id else None,
                connector=self.control_connector,
            )
        )
        await self._stop_ws_gateway()
        await self._start_ws_gateway()

        await self._scheduler.stop()
        await self._worker.stop()
        self._worker = Worker(
            config=config,
            db=self.db,
            queue=self.queue,
            tool_registry=tool_registry,
            policy=policy,
            skill_registry=skill_registry,
            unified_registry=unified_registry,
            send_message=self.send_message,
            send_control_message=self.send_control_message,
        )
        await self._worker.start()
        await self._scheduler.start()
        logger.info("Reload complete")

    async def send_message(self, channel: str, chat_id: str, text: str, connector: str = "") -> None:
        if not await self._hub.send(channel, connector, chat_id, text):
            logger.warning("No connector available for channel=%s connector=%s", channel, connector)

    async def send_control_message(self, fallback_channel: str, fallback_chat_id: str, text: str) -> None:
        """Send message to control panel, with fallback to original channel."""
        # Try new control panel first
        if self._control_panel.config.enabled:
            await self._control_panel.send_notification(text)
            return

        # DEPRECATED: Fall back to old control_* fields for backward compatibility
        if self.control_channel and self.control_chat_id:
            await self.send_message(
                self.control_channel,
                str(self.control_chat_id),
                text,
                connector=self.control_connector or "",
            )
            return

        # Final fallback: send to original channel
        await self.send_message(fallback_channel, fallback_chat_id, text)

    async def _on_message(self, message) -> None:
        routed = self._router.route(message)
        if routed.kind == "control":
            logger.debug("Control message received channel=%s chat_id=%s", message.channel, message.chat_id)
        else:
            logger.debug("External message received channel=%s chat_id=%s", message.channel, message.chat_id)

        pending = routed.pending_confirmation
        if pending:
            await self.queue.enqueue(
                pending.chat_id,
                pending.channel,
                {
                    "type": "confirm",
                    "pending": {
                        "chat_id": pending.chat_id,
                        "channel": pending.channel,
                        "session_id": pending.session_id,
                        "message_id": pending.message_id,
                        "tool_call": pending.tool_call,
                        "messages": pending.messages,
                    },
                },
            )
            self.db.add_audit(
                "tool_confirmation_accepted",
                {"chat_id": pending.chat_id, "tool": pending.tool_call.get("name")},
            )
            return

        session_id = self.db.get_or_create_session(message.chat_id, message.channel)
        self.db.add_message(session_id, "user", message.text)
        await self.queue.enqueue(
            message.chat_id,
            message.channel,
            {
                "type": "message",
                "kind": routed.kind,
                "connector": "",
                "chat_id": message.chat_id,
                "channel": message.channel,
                "session_id": session_id,
                "text": message.text,
            },
        )

    async def _on_ws_event(self, connector: str, channel: str, mode: str, data: dict) -> None:
        message = IncomingMessage(
            channel=channel,
            chat_id=str(data.get("chat_id", "")),
            user_id=str(data.get("user_id", "")),
            text=str(data.get("text", "")),
            connector=connector,
        )
        kind_hint = "control" if mode == "control" else None
        routed = self._router.route(message, kind_hint=kind_hint)
        if routed.kind == "control":
            logger.debug("Control message received channel=%s chat_id=%s", message.channel, message.chat_id)
        else:
            logger.debug("External message received channel=%s chat_id=%s", message.channel, message.chat_id)

        pending = routed.pending_confirmation
        if pending:
            await self.queue.enqueue(
                pending.chat_id,
                pending.channel,
                {
                    "type": "confirm",
                    "pending": {
                        "chat_id": pending.chat_id,
                        "channel": pending.channel,
                        "session_id": pending.session_id,
                        "message_id": pending.message_id,
                        "tool_call": pending.tool_call,
                        "messages": pending.messages,
                    },
                },
            )
            self.db.add_audit(
                "tool_confirmation_accepted",
                {"chat_id": pending.chat_id, "tool": pending.tool_call.get("name")},
            )
            return

        session_id = self.db.get_or_create_session(message.chat_id, message.channel, connector=connector)
        self.db.add_message(session_id, "user", message.text)
        await self.queue.enqueue(
            message.chat_id,
            message.channel,
            {
                "type": "message",
                "kind": routed.kind,
                "connector": connector,
                "chat_id": message.chat_id,
                "channel": message.channel,
                "session_id": session_id,
                "text": message.text,
            },
        )

    def _on_connector_status(self, connector: str, channel: str, mode: str, status: str) -> None:
        logger.info("Connector %s channel=%s mode=%s status=%s", connector, channel, mode, status)
        self.db.update_connector_status(connector, channel, mode, status)

    async def _start_ws_gateway(self) -> None:
        token = self.config.runtime.ws_token or ""
        if not token:
            logger.warning("WebSocket gateway disabled (UMABOT_WS_TOKEN not set)")
            return
        self._ws_gateway = WebsocketGateway(
            host=self.config.runtime.ws_host,
            port=int(self.config.runtime.ws_port),
            token=token,
            hub=self._hub,
        )
        await self._ws_gateway.start(self._on_ws_event)
        logger.info(
            "WebSocket gateway started ws://%s:%s/ws",
            self.config.runtime.ws_host,
            self.config.runtime.ws_port,
        )

    async def _stop_ws_gateway(self) -> None:
        if self._ws_gateway:
            await self._ws_gateway.stop()
            self._ws_gateway = None


def build_runtime(config_path: Optional[str] = None, overrides: Optional[Dict[str, str]] = None):
    config, resolved_path = load_config(config_path=config_path, overrides=overrides)
    db = Database(config.storage.db_path)
    queue = Queue(db)
    skill_registry = SkillRegistry()
    skill_registry.load_from_dirs([
        Path.cwd() / "skills",
        Path.home() / ".umabot" / "skills",
    ])

    # Create skill runtime
    skill_runtime = SkillRuntime(skill_registry=skill_registry, config=config)

    # Legacy tool registry (for PolicyEngine compatibility)
    tool_registry = ToolRegistry()
    register_builtin_tools(
        tool_registry,
        enable_shell=config.tools.shell_enabled,
    )
    register_skill_tools(
        tool_registry,
        runtime=skill_runtime,
    )

    # New unified tool registry
    unified_registry = UnifiedToolRegistry()

    # Register built-in tools
    for tool in tool_registry.list().values():
        unified_registry.register_builtin(tool)

    # Connect skill system
    unified_registry.set_skill_registry(skill_registry)
    unified_registry.set_skill_runtime(skill_runtime)

    policy = PolicyEngine(tool_registry, strictness=config.policy.confirmation_strictness)
    return config, resolved_path, db, queue, tool_registry, policy, skill_registry, unified_registry


def _reload_runtime(config_path: str, overrides: Optional[Dict[str, str]] = None):
    config, resolved_path = load_config(config_path=config_path, overrides=overrides)
    skill_registry = SkillRegistry()
    skill_registry.load_from_dirs([
        Path.cwd() / "skills",
        Path.home() / ".umabot" / "skills",
    ])

    # Create skill runtime
    skill_runtime = SkillRuntime(skill_registry=skill_registry, config=config)

    # Legacy tool registry (for PolicyEngine compatibility)
    tool_registry = ToolRegistry()
    register_builtin_tools(
        tool_registry,
        enable_shell=config.tools.shell_enabled,
    )
    register_skill_tools(
        tool_registry,
        runtime=skill_runtime,
    )

    # New unified tool registry
    unified_registry = UnifiedToolRegistry()

    # Register built-in tools
    for tool in tool_registry.list().values():
        unified_registry.register_builtin(tool)

    # Connect skill system
    unified_registry.set_skill_registry(skill_registry)
    unified_registry.set_skill_runtime(skill_runtime)

    policy = PolicyEngine(tool_registry, strictness=config.policy.confirmation_strictness)
    return config, resolved_path, tool_registry, policy, skill_registry, unified_registry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", dest="config", default=None)
    parser.add_argument("--log-file", dest="log_file", default=None)
    parser.add_argument(
        "--log-level",
        dest="log_level",
        default=None,
        help="Logging level (DEBUG, INFO, WARNING, ERROR). Can also use UMABOT_LOG_LEVEL.",
    )
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override config key (section.field=value or UMABOT_ENV=value).",
    )
    args = parser.parse_args()

    log_level = _resolve_log_level(args.log_level)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        filename=args.log_file,
    )

    override_map = _parse_overrides(args.overrides)
    config, config_path, db, queue, tool_registry, policy, skill_registry, unified_registry = build_runtime(
        config_path=args.config,
        overrides=override_map,
    )

    gateway = Gateway(
        config=config,
        config_path=config_path,
        overrides=override_map,
        db=db,
        queue=queue,
        tool_registry=tool_registry,
        policy=policy,
        skill_registry=skill_registry,
        unified_registry=unified_registry,
    )

    async def runner():
        await gateway.start()
        stop_event = asyncio.Event()
        reload_event = asyncio.Event()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop_event.set)
        if hasattr(signal, "SIGHUP"):
            loop.add_signal_handler(signal.SIGHUP, reload_event.set)

        while not stop_event.is_set():
            if reload_event.is_set():
                reload_event.clear()
                await gateway.reload()
            await asyncio.sleep(0.5)

        await gateway.stop()

    asyncio.run(runner())


def _parse_overrides(values: list[str]) -> Dict[str, str]:
    if not values:
        return {}


def _resolve_log_level(value: Optional[str]) -> int:
    raw = value or os.environ.get("UMABOT_LOG_LEVEL", "INFO")
    level = logging.getLevelName(str(raw).upper())
    if isinstance(level, int):
        return level
    logger.warning("Invalid log level %s, defaulting to INFO", raw)
    return logging.INFO
    try:
        return parse_override_args(values)
    except Exception as exc:
        logger.warning("Invalid --set override: %s", exc)
        return {}


if __name__ == "__main__":
    main()
