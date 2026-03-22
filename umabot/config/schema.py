from __future__ import annotations

import sys
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
    # For OpenAI reasoning models (o1, o3, o4-mini etc.)
    # Valid values: "low" | "medium" | "high" | None (uses model default)
    reasoning_effort: Optional[str] = None


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
class WorkerConfig:
    """Controls worker concurrency only."""

    # How many jobs to process concurrently (across different chat_ids).
    # Jobs for the same chat_id are always serialized regardless of this value.
    concurrency: int = 1


@dataclass
class SkillRuntimeOverride:
    """Runtime overrides for a single skill or as global defaults under skills.defaults.

    Example config.yaml:
        skills:
          defaults:                                    # global, applied to every skill
            node_bin: ~/.nvm/versions/node/v24.3.0/bin
          docx:                                        # per-skill
            env:
              NODE_ENV: production
          news:
            env:
              SERPAPI_API_KEY: your-key-here
    """

    # Path to directory containing the `node` binary (e.g. ~/.nvm/versions/node/v20/bin).
    node_bin: str = ""

    # Override the Python binary used when provisioning skill venvs.
    # Default: sys.executable.
    python_bin: str = ""

    # Extra directories prepended to PATH when running this skill.
    extra_path: List[str] = field(default_factory=list)

    # Environment variables injected into every subprocess call for this skill.
    # Per-skill env is merged on top of defaults env (per-skill wins on conflicts).
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class SkillsConfig:
    """Top-level skills configuration block.

    Structure in config.yaml::

        skills:
          defaults:             # global runtime defaults applied to all skills
            node_bin: ~/.nvm/versions/node/v24.3.0/bin
            python_bin: ""
            extra_path: []
            env: {}

          docx:                 # per-skill block, keyed by skill name
            node_bin: ""        # leave empty to inherit defaults
            env:
              OUTPUT_DIR: ~/Documents

          news:
            env:
              SERPAPI_API_KEY: ""   # put your key here

    Precedence (highest wins):
        SKILL.md runtime: → skills.defaults → skills.<name>
    """

    # Global runtime defaults applied to every skill
    defaults: SkillRuntimeOverride = field(default_factory=SkillRuntimeOverride)

    # Per-skill overrides, keyed by skill name.
    # Populated dynamically from any unknown key under `skills:` in config.yaml.
    # Not a dataclass field — managed by the custom loader.
    _per_skill: Dict[str, SkillRuntimeOverride] = field(default_factory=dict, repr=False)

    def get_skill_override(self, skill_name: str) -> Optional[SkillRuntimeOverride]:
        """Return the per-skill override for skill_name, or None if not configured."""
        return self._per_skill.get(skill_name)

    def set_skill_override(self, skill_name: str, override: SkillRuntimeOverride) -> None:
        self._per_skill[skill_name] = override

    def iter_skill_overrides(self):
        return self._per_skill.items()


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
class AgentModelConfig:
    """LLM config for a specific agent role within the orchestration system.

    Empty string / None means inherit from the top-level ``llm`` block.
    """

    provider: str = ""             # "" → inherit llm.provider
    model: str = ""                # "" → inherit llm.model
    api_key: Optional[str] = None  # None → inherit llm.api_key
    reasoning_effort: Optional[str] = None


@dataclass
class AgentsConfig:
    """Dynamic multi-agent orchestration settings.

    When enabled, every user request is first routed to a strong
    orchestrator LLM which dynamically decides what specialist agents
    are needed.  Each spawned agent runs its own agentic tool loop.

    Example config.yaml::

        agents:
          enabled: true
          orchestrator:
            provider: openai
            model: o3
            reasoning_effort: medium
          worker:
            provider: openai
            model: gpt-4o
    """

    enabled: bool = False
    orchestrator: AgentModelConfig = field(default_factory=AgentModelConfig)
    worker: AgentModelConfig = field(default_factory=AgentModelConfig)
    # Maximum tool-call iterations per spawned agent
    max_agent_iterations: int = 15
    # Maximum tool-call iterations for the orchestrator itself
    max_orchestrator_iterations: int = 20
    # Shared token-per-minute budget enforced across all LLM clients.
    # 0 = disabled (no throttling).  Set to your API tier limit, e.g. 25000.
    tokens_per_minute: int = 0


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    control_panel: ControlPanelConfig = field(default_factory=ControlPanelConfig)
    connectors: List[ConnectorConfig] = field(default_factory=list)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    skill_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    skill_dirs: List[str] = field(default_factory=list)  # Additional skill directories
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
        _resolve_skill_runtime_override(self.skills.defaults)
        if not self.skills.defaults.python_bin:
            self.skills.defaults.python_bin = sys.executable
        for override in self.skills._per_skill.values():
            _resolve_skill_runtime_override(override)


def _expand_path(path: str) -> str:
    return str(Path(path).expanduser())


def _resolve_skill_runtime_override(o: "SkillRuntimeOverride") -> None:
    if o.node_bin:
        o.node_bin = _expand_path(o.node_bin)
    if o.python_bin:
        o.python_bin = _expand_path(o.python_bin)
    o.extra_path = [_expand_path(p) for p in o.extra_path]


def default_config() -> Config:
    cfg = Config()
    cfg.resolve_paths()
    return cfg
