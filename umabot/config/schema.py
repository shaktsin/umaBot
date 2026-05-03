from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PanelObservabilityConfig:
    """Per-panel visibility controls for operational telemetry streams."""

    # none | summary | full
    multi_agent_topology: str = "summary"
    # none | summary | full
    multi_agent_logs: str = "none"


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
    # Per-panel observability controls (web/telegram/discord may differ)
    observability: PanelObservabilityConfig = field(default_factory=PanelObservabilityConfig)


# Connector types that are inbound-only (listener role).
# Everything else is treated as admin (bidirectional, trusted).
_LISTENER_TYPES: frozenset = frozenset({"gmail_imap", "telegram_user", "discord"})


@dataclass
class ConnectorConfig:
    name: str
    type: str  # telegram_bot | telegram_user | discord | gmail_imap
    # Telegram bot / user fields
    token: Optional[str] = None
    api_id: Optional[str] = None
    api_hash: Optional[str] = None
    session_name: Optional[str] = None
    phone: Optional[str] = None
    allow_login: bool = False
    # Gmail IMAP connector fields
    mailbox: str = "INBOX"           # IMAP mailbox to watch (default: INBOX)
    reply_connector: str = ""        # connector to route LLM responses to (e.g. control_panel_bot)
    reply_chat_id: str = ""          # chat_id to route LLM responses to (e.g. owner Telegram ID)
    reply_channel: str = "telegram"  # channel type of the reply target
    # Auto-assigned from type — never set manually in config.yaml.
    # "listener" = inbound-only, PII-filtered, pinned to admin session.
    # "admin"    = bidirectional, trusted, receives notifications.
    role: str = ""

    def __post_init__(self) -> None:
        if not self.role:
            self.role = "listener" if self.type in _LISTENER_TYPES else "admin"


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: Optional[str] = None
    # For OpenAI reasoning models (o1, o3, o4-mini etc.)
    # Valid values: "low" | "medium" | "high" | None (uses model default)
    reasoning_effort: Optional[str] = None


@dataclass
class LLMProviderConfig:
    """Configuration for a single LLM provider in the admin registry."""

    enabled: bool = True
    api_key: Optional[str] = None
    models: List[str] = field(default_factory=list)
    default_model: str = ""


def _default_llm_providers() -> Dict[str, "LLMProviderConfig"]:
    return {
        "openai": LLMProviderConfig(
            enabled=True,
            models=["gpt-5.4-mini", "gpt-4o-mini", "o4-mini"],
            default_model="gpt-5.4-mini",
        ),
        "claude": LLMProviderConfig(
            enabled=True,
            models=["claude-sonnet-4-6", "claude-opus-4-6"],
            default_model="claude-sonnet-4-6",
        ),
        "gemini": LLMProviderConfig(
            enabled=True,
            models=["gemini-2.5-pro", "gemini-2.5-flash"],
            default_model="gemini-2.5-flash",
        ),
    }


@dataclass
class ChannelConfig:
    enabled: bool = False
    token: Optional[str] = None


@dataclass
class GoogleConfig:
    """Google Workspace integration (Gmail, Calendar, Tasks).

    Create a GCP OAuth 2.0 'Web application' credential and paste the values here
    or set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET environment variables.
    """

    client_id: str = ""
    client_secret: str = ""


@dataclass
class IntegrationsConfig:
    """Third-party service integrations the bot can act on.

    Distinct from *connectors* (messaging channels the bot listens on) and
    *skills* (domain knowledge). Integrations are external services the LLM
    calls via built-in tools (gmail.*, gcal.*, gtasks.*, github.*, …).

    Example config.yaml::

        integrations:
          google:
            client_id: "123456789.apps.googleusercontent.com"
            client_secret: "GOCSPX-..."
    """

    google: GoogleConfig = field(default_factory=GoogleConfig)


@dataclass
class SecurityRoleConfig:
    """Per-role tool permissions."""

    # Tool name patterns allowed for this role (supports fnmatch globs, e.g. 'gmail.*')
    allow: List[str] = field(default_factory=lambda: ["*"])
    # Tool name patterns always denied for this role (takes precedence over allow)
    deny: List[str] = field(default_factory=list)
    # Whether RISK_RED tools require explicit approval for this role
    require_approval_for_red: bool = True
    # Whether RISK_YELLOW tools require explicit approval for this role
    require_approval_for_yellow: bool = False


@dataclass
class SecurityConfig:
    """Security policy layer.

    Controls tool access per connector, per user, and enables SSRF protection
    and credential masking in tool output.

    Example config.yaml::

        security:
          ssrf_protection: true
          mask_secrets_in_output: true
          roles:
            admin:
              allow: ["*"]
              require_approval_for_red: false
            user:
              deny: ["shell.*"]
              require_approval_for_red: true
          users:
            "123456789":   # Telegram user_id → role name
              role: admin
          connectors:
            internal-bot:
              default_role: admin
    """

    enabled: bool = True
    ssrf_protection: bool = True
    mask_secrets_in_output: bool = True

    # Named role definitions
    roles: Dict[str, Any] = field(default_factory=dict)

    # user_id → {"role": "admin"} mapping
    users: Dict[str, Any] = field(default_factory=dict)

    # connector_name → {"default_role": "user"} overrides
    connectors: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkspaceACL:
    """Fine-grained access control for a workspace directory.

    Each flag independently gates a class of operation.  For example you can
    allow reads (browsing build output) while denying deletes.
    """

    read: bool = True           # file.read / file.list
    write: bool = True          # file.write (modify existing)
    create_files: bool = True   # file.write (create new)
    delete_files: bool = False  # file.delete
    shell: bool = True          # shell.run may use this dir as cwd


@dataclass
class WorkspaceConfig:
    """A named, sandboxed directory umabot may operate in.

    Example config.yaml::

        tools:
          workspaces:
            - name: projects
              path: ~/projects
              default: true
              acl:
                read: true
                write: true
                create_files: true
                delete_files: false
                shell: true
            - name: downloads
              path: ~/Downloads
              acl:
                read: true
                write: false
                create_files: false
                delete_files: false
                shell: false
    """

    name: str = ""
    path: str = ""
    acl: WorkspaceACL = field(default_factory=WorkspaceACL)
    default: bool = False   # used when user doesn't specify a workspace


@dataclass
class ToolsConfig:
    shell_enabled: bool = False
    workspaces: List[WorkspaceConfig] = field(default_factory=list)


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP (Model Context Protocol) server.

    Two transports are supported:

    ``stdio`` (default) — umabot spawns the server as a child process and
    communicates via stdin/stdout.  Use for local servers installed via npm/pip.

    ``http`` — the server is already running (e.g. in Docker) and umabot
    connects over HTTP.  Uses the MCP Streamable HTTP transport: each
    JSON-RPC request is a POST to ``url`` and the response is either plain
    JSON or an SSE stream, depending on what the server returns.

    Example config.yaml::

        mcp_servers:
          # stdio — umabot spawns this
          - name: playwright
            transport: stdio          # default, can be omitted
            command: npx
            args: ["@playwright/mcp@latest", "--headless"]

          # http — already running in Docker
          - name: my_docker_mcp
            transport: http
            url: http://localhost:8080
    """

    name: str = ""
    # "stdio" (default) or "http"
    transport: str = "stdio"
    # --- stdio fields ---
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, Any] = field(default_factory=dict)
    # Forward listed env vars from parent process into stdio subprocess env.
    env_vars: List[str] = field(default_factory=list)
    # Optional subprocess working directory for stdio transport.
    cwd: str = ""
    # --- http fields ---
    url: str = ""                 # e.g. "http://localhost:8080"
    # Reads token from env and injects Authorization: Bearer <token>.
    bearer_token_env_var: str = ""
    # Static headers sent on every HTTP request.
    http_headers: Dict[str, Any] = field(default_factory=dict)
    # Header-name -> env-var-name mapping resolved at runtime.
    env_http_headers: Dict[str, Any] = field(default_factory=dict)
    # --- common ---
    enabled: bool = True
    # If true, startup/reload fails when this server cannot be initialized.
    required: bool = False
    # Timeouts (seconds)
    startup_timeout_sec: float = 10.0
    tool_timeout_sec: float = 60.0
    # Tool allow/deny controls (applied to discovered MCP tool names)
    enabled_tools: List[str] = field(default_factory=list)
    disabled_tools: List[str] = field(default_factory=list)
    # Optional OAuth callback overrides for HTTP MCP auth flows.
    mcp_oauth_callback_port: int = 0
    mcp_oauth_callback_url: str = ""

    def __post_init__(self) -> None:
        self.transport = (self.transport or "stdio").strip().lower()
        if self.startup_timeout_sec <= 0:
            self.startup_timeout_sec = 10.0
        if self.tool_timeout_sec <= 0:
            self.tool_timeout_sec = 60.0


@dataclass
class PolicyRuleMatchConfig:
    """Match conditions for a single declarative policy rule.

    Every field is optional; empty values mean "match all".
    Pattern fields support fnmatch globs, e.g. "gmail_*" or "gmail.*".
    """

    connectors: List[str] = field(default_factory=list)
    connector_roles: List[str] = field(default_factory=list)  # listener | admin
    channels: List[str] = field(default_factory=list)         # gmail | telegram | web | ...
    directions: List[str] = field(default_factory=list)       # inbound | outbound
    kinds: List[str] = field(default_factory=list)            # external | control | task_run
    tools: List[str] = field(default_factory=list)            # gmail.read, gmail.*, shell.*
    importance: List[str] = field(default_factory=list)       # high | medium | low
    actions: List[str] = field(default_factory=list)          # summarize | draft_reply | create_task | ignore
    needs_admin: Optional[bool] = None
    admin_explicit: Optional[bool] = None


@dataclass
class PolicyRuleApplyConfig:
    """Effects produced when a policy rule matches."""

    # inherit | allow | deny | require_confirmation
    tool: str = "inherit"
    # Whether this inbound message may be sent to the main LLM pipeline.
    ingest_to_llm: Optional[bool] = None
    # Optional intent overrides (listener flow).
    set_importance: str = ""
    set_action: str = ""
    set_needs_admin: Optional[bool] = None
    # Human-readable reason shown in logs / denial messages.
    reason: str = ""


@dataclass
class PolicyRuleConfig:
    """Declarative policy rule definition.

    Rules are evaluated by ascending ``priority`` (lower value runs first),
    then by declaration order.
    """

    id: str = ""
    enabled: bool = True
    priority: int = 100
    description: str = ""
    match: PolicyRuleMatchConfig = field(default_factory=PolicyRuleMatchConfig)
    apply: PolicyRuleApplyConfig = field(default_factory=PolicyRuleApplyConfig)


@dataclass
class PolicyConfig:
    confirmation_strictness: str = "normal"  # normal | strict
    # normal | auto_approve_workspace
    # auto_approve_workspace bypasses approval prompts for eligible tool calls
    # that stay inside explicitly allowed workspaces.
    approval_mode: str = "normal"
    auto_approve_workspaces: List[str] = field(default_factory=list)
    auto_approve_tools: List[str] = field(default_factory=list)
    auto_approve_shell_commands: List[str] = field(default_factory=list)
    # Optional external file containing policy rules to keep config.yaml small.
    # Supports either:
    #   1) top-level "rules: [...]"
    #   2) direct list "[...]"
    rules_file: str = ""
    # Inline declarative rules.
    rules: List[PolicyRuleConfig] = field(default_factory=list)


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
          context_file: ~/.umabot/AGENT.md   # optional — user-defined agent context
          orchestrator:
            provider: openai
            model: o3
            reasoning_effort: medium
          worker:
            provider: openai
            model: gpt-4o
    """

    enabled: bool = False
    # Path to a Markdown file whose content is injected into every agent's
    # system prompt.  Edit this file to give the bot a persona, standing
    # instructions, personal facts, or domain context.
    context_file: str = "~/.umabot/AGENT.md"
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
class AgentTeamsConfig:
    """Reusable team-registry settings layered on top of dynamic orchestration."""

    enabled: bool = False
    routing_mode: str = "hybrid"  # rule | llm | hybrid
    default_confidence_threshold: float = 0.62
    fallback_to_dynamic: bool = True
    max_teams_considered: int = 20
    fit_gate_enabled: bool = True
    fit_min_query_len: int = 60
    # Additional directories scanned for TEAM.md packs
    team_dirs: List[str] = field(default_factory=list)
    # Writable install target used by control panel install/create actions
    install_dir: str = "~/.umabot/agent-teams"


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    llm_providers: Dict[str, LLMProviderConfig] = field(default_factory=_default_llm_providers)
    # Single primary control panel (kept for backward compat with existing configs).
    # When multiple panels are needed (e.g. web + telegram) populate control_panels
    # list instead — or use both: primary + extras.
    control_panel: ControlPanelConfig = field(default_factory=ControlPanelConfig)
    # Additional control panels that run alongside the primary one.
    # Every enabled panel receives all notifications simultaneously.
    control_panels: List[ControlPanelConfig] = field(default_factory=list)
    connectors: List[ConnectorConfig] = field(default_factory=list)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    agent_teams: AgentTeamsConfig = field(default_factory=AgentTeamsConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    skill_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    skill_dirs: List[str] = field(default_factory=list)  # Additional skill directories
    integrations: IntegrationsConfig = field(default_factory=IntegrationsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    mcp_servers: List[MCPServerConfig] = field(default_factory=list)

    # Deprecated flat field — kept so existing configs with `google:` still load
    google: GoogleConfig = field(default_factory=GoogleConfig)
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
        if self.policy.rules_file:
            self.policy.rules_file = _expand_path(self.policy.rules_file)
        if self.agents.context_file:
            self.agents.context_file = _expand_path(self.agents.context_file)
        self.agent_teams.install_dir = _expand_path(self.agent_teams.install_dir)
        self.agent_teams.team_dirs = [_expand_path(p) for p in self.agent_teams.team_dirs]
        _resolve_skill_runtime_override(self.skills.defaults)
        if not self.skills.defaults.python_bin:
            self.skills.defaults.python_bin = sys.executable
        for override in self.skills._per_skill.values():
            _resolve_skill_runtime_override(override)
        for ws in self.tools.workspaces:
            if ws.path:
                ws.path = _expand_path(ws.path)
        for server in self.mcp_servers:
            if server.cwd:
                server.cwd = _expand_path(server.cwd)


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


def get_connector_role(config: "Config", connector_name: str) -> str:
    """Return 'listener' or 'admin' for the named connector.

    Falls back to 'admin' if the connector is not found in config (e.g. the
    built-in web-panel pseudo-connector).
    """
    for c in config.connectors:
        if c.name == connector_name:
            return c.role
    return "admin"
