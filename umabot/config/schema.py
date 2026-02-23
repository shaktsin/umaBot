from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ControlPanelConfig:
    """
    Owner's private interface for confirmations and management.

    The control panel is decoupled from connectors - it can be:
    - CLI chat UI (local terminal interface)
    - Web UI (local browser interface)
    - Telegram bot (remote messaging)
    - Discord bot (remote messaging)
    - Any other UI type
    """

    enabled: bool = False
    ui_type: str = "telegram"  # telegram | discord | cli | web

    # For messaging-based UIs (telegram, discord)
    connector: str = ""  # Reference to connector name (if using messaging UI)
    chat_id: Optional[str] = None  # Owner's chat/channel ID

    # For local UIs (cli, web)
    web_host: str = "127.0.0.1"
    web_port: int = 5000


@dataclass
class ConnectorConfig:
    name: str
    type: str  # telegram_bot | telegram_user | discord | whatsapp
    token: Optional[str] = None
    api_id: Optional[str] = None
    api_hash: Optional[str] = None
    session_name: Optional[str] = None
    phone: Optional[str] = None
    allow_login: bool = False


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: Optional[str] = None


@dataclass
class ChannelConfig:
    enabled: bool = False
    token: Optional[str] = None


@dataclass
class ToolsConfig:
    shell_enabled: bool = False


@dataclass
class PolicyConfig:
    confirmation_strictness: str = "normal"  # normal | strict


@dataclass
class StorageConfig:
    db_path: str = "~/.umabot/umabot.db"
    vault_dir: str = "~/.umabot/vault"


@dataclass
class RuntimeConfig:
    pid_file: str = "~/.umabot/umabot.pid"
    log_dir: str = "~/.umabot/logs"
    # DEPRECATED: control_* fields moved to ControlPanelConfig
    # Kept for backward compatibility
    control_channel: str = ""
    control_chat_id: Optional[str] = None
    control_connector: Optional[str] = None
    ws_host: str = "127.0.0.1"
    ws_port: int = 8765
    ws_token: Optional[str] = None


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    control_panel: ControlPanelConfig = field(default_factory=ControlPanelConfig)
    connectors: List[ConnectorConfig] = field(default_factory=list)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    skill_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # DEPRECATED: telegram/discord/whatsapp fields - use connectors list instead
    # Kept for backward compatibility
    telegram: ChannelConfig = field(default_factory=ChannelConfig)
    discord: ChannelConfig = field(default_factory=ChannelConfig)
    whatsapp: ChannelConfig = field(default_factory=ChannelConfig)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def resolve_paths(self) -> None:
        self.storage.db_path = _expand_path(self.storage.db_path)
        self.storage.vault_dir = _expand_path(self.storage.vault_dir)
        self.runtime.pid_file = _expand_path(self.runtime.pid_file)
        self.runtime.log_dir = _expand_path(self.runtime.log_dir)


def _expand_path(path: str) -> str:
    return str(Path(path).expanduser())


def default_config() -> Config:
    cfg = Config()
    cfg.resolve_paths()
    return cfg
