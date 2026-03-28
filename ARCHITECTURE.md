# UmaBot Architecture

Reference for contributors and advanced users. Covers internals, message flow, tool routing, deployment, and extension points.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            USER INTERFACES                                  │
│                                                                             │
│  👤 Owner (Control Panel)          👥 External Users                        │
│  Telegram / Web                    Telegram / Discord                       │
│        │                                   │                               │
│        └──────────────┬────────────────────┘                               │
└─────────────────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      CONNECTOR LAYER (Out-of-Process)                       │
│                                                                             │
│  ┌───────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │ Control Panel │  │ Telegram Bot   │  │ Telegram User  │  …              │
│  │   Connector   │  │   Connector    │  │   Connector    │                 │
│  └──────┬────────┘  └──────┬─────────┘  └──────┬─────────┘                 │
│         └──────────────────┴──────────────────┘                            │
│                             │                                               │
│                    ┌────────▼────────┐                                      │
│                    │  WebSocket Hub  │  :8765  (token-authenticated)        │
│                    └────────┬────────┘                                      │
└─────────────────────────────┼───────────────────────────────────────────────┘
                               │  ws://
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           GATEWAY PROCESS                                   │
│                                                                             │
│  WebSocket Gateway ──▶ Message Router ──▶ Control Panel Manager            │
│                                │                                            │
│                                ▼                                            │
│                        Message Queue (SQLite)                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           WORKER PROCESS                                    │
│                                                                             │
│  Worker Event Loop                                                          │
│       │                                                                     │
│       ├──▶ LLM Client (Claude / OpenAI / Gemini)                            │
│       ├──▶ Policy Engine  🟢🟡🔴                                             │
│       ├──▶ Unified Tool Registry                                            │
│       │       ├── Built-in tools  (shell.run, file.*, web.*)                │
│       │       ├── Skill tools     (isolated subprocess + venv)              │
│       │       └── MCP tools       (JSON-RPC to external servers)            │
│       └──▶ Task Scheduler  (cron + one-time tasks)                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            STORAGE LAYER                                    │
│                                                                             │
│  SQLite DB              Vault Dir              Skills Dirs                  │
│  • messages             • sensitive files      • SKILL.md manifests         │
│  • sessions             • oauth tokens         • per-skill .venv            │
│  • tasks / task_runs                           • scripts/                   │
│  • audit_log                                                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Key Components

| Component | Purpose | Where it runs |
|-----------|---------|--------------|
| **Connectors** | Receive/send messages for each channel | Out-of-process workers |
| **WebSocket Hub** | Routes messages between connectors and gateway | Subprocess inside gateway |
| **Message Router** | Classifies messages as control vs external | Gateway main loop |
| **Control Panel Manager** | Dispatches approval requests to the owner | Gateway |
| **Message Queue** | SQLite-backed durable job queue | Shared between gateway and worker |
| **Worker** | Claims jobs, runs LLM + tool loop | Async event loop |
| **LLM Client** | Provider-agnostic Claude/OpenAI/Gemini wrapper | Worker |
| **Policy Engine** | Assigns risk tier, manages confirmation tokens | Worker |
| **Unified Tool Registry** | Routes tool calls to built-ins, skills, or MCP | Worker |
| **Task Scheduler** | Enqueues periodic and one-time tasks | Separate asyncio loop |
| **Skill Registry** | Loads and indexes SKILL.md manifests | Startup + hot-reload |

---

## Message Flow

```
User sends message
      │
      ├─ [1]  Connector receives (Telegram long-poll / Discord WS)
      ├─ [2]  Sent over WebSocket to Gateway Hub
      ├─ [3]  Message Router classifies:
      │          control message?  → Control Panel Manager
      │          external message? → Message Queue (SQLite)
      ├─ [4]  Worker claims job from queue
      ├─ [5]  Worker detects workspace from message text; sets active workspace
      ├─ [6]  LLM receives message + system prompt (skills catalog, workspace catalog)
      │
      │  LLM tool loop:
      ├─ [7]  LLM requests a tool call
      ├─ [8]  Policy Engine checks risk tier
      │          🟢 GREEN  → auto-approve
      │          🟡 YELLOW → auto-approve (strict mode: approval required)
      │          🔴 RED    → send approval request to control panel; block until response
      ├─ [9]  Tool executes (in-process / subprocess / JSON-RPC)
      ├─ [10] Result returned to LLM; loop continues until no more tool calls
      │
      ├─ [11] LLM generates final response
      ├─ [12] Response routed back via WebSocket Hub → original connector
      └─ [13] User receives reply
```

---

## Tool Routing

```
LLM calls: tool_name(args)
      │
      ▼
UnifiedToolRegistry.execute_tool(name, args)
      │
      ├─ built-in prefix  →  run async function in-process
      │                       e.g. shell.run, file.write, file.read
      │
      ├─ skill_ prefix    →  spawn subprocess in skill's own .venv
      │                       stdin/stdout JSON protocol
      │                       e.g. skill_github_create_pr
      │
      └─ mcp_ prefix      →  HTTP JSON-RPC to external MCP server
                              e.g. mcp_filesystem_read_file
```

**Tool naming convention:**
- Built-in: `category.action` (e.g. `shell.run`, `file.write`)
- Skill: `skill_<name>_<script>` (e.g. `skill_github_create_pr`)
- MCP: `mcp_<server>_<tool>` (e.g. `mcp_github_create_issue`)

---

## Multi-Agent Orchestration

When agents are enabled, the `DynamicOrchestrator` handles complex tasks:

1. Orchestrator receives the user message + full skills catalog + workspace catalog
2. Orchestrator spawns `SpawnedAgent` workers via the `spawn_agent` tool
3. Each spawned agent gets: a focused sub-task, a workspace assignment, a tool subset
4. Agents run their own LLM + tool loops and return structured results
5. Orchestrator synthesises results and sends the final reply

Spawned agents inherit the active workspace; `set_active_workspace` is called before each agent runs so ContextVar isolation works correctly across concurrent asyncio tasks.

---

## Workspace ACL

Every file and shell operation is checked against the active workspace:

```
enforce_path(path, workspace, operation)
  ├─ containment check: path must be inside workspace.path
  └─ ACL check:
       file.read   → acl.read
       file.write  → acl.write
       file create → acl.create_files
       file delete → acl.delete_files
       shell.run   → acl.shell
```

The active workspace is stored in a `ContextVar` so concurrent worker tasks (different `chat_id`s) each see their own workspace without interference.

---

## Skill Loading

At startup and on `make reload`:

1. All `skill_dirs` are scanned for subdirectories containing `SKILL.md`
2. Each `SKILL.md` is parsed for name, description, runtime config, and tool definitions
3. Tools are registered in `UnifiedToolRegistry` under the `skill_<name>_` prefix
4. The orchestrator receives a summarised skills catalog in its system prompt
5. Per-skill env overrides from `skill_configs` in `config.yaml` are applied at call time

**Progressive disclosure:** the orchestrator sees a one-line summary per skill. When it wants details for a specific skill it calls `skill.get_instructions` which returns the full `SKILL.md`.

---

## Security Layers

| Layer | Mechanism |
|-------|----------|
| **Input validation** | JSON Schema on every tool call argument |
| **Risk tiers** | GREEN / YELLOW / RED assigned per tool |
| **RED confirmation** | Single-use 128-bit token, expires on use or timeout |
| **Workspace containment** | All file/shell ops checked against workspace ACL |
| **Skill isolation** | Each skill runs in its own virtualenv subprocess |
| **Secret stripping** | API keys / tokens nulled out before writing `config.yaml` |
| **Secret masking** | Tokens masked as `***last4` in all log output |
| **SSRF protection** | Outbound HTTP blocked to RFC1918, 169.254.x.x, metadata IPs |

---

## WebSocket Protocol

Connectors connect to `ws://127.0.0.1:8765` with a bearer token (`runtime.ws_token`).

Messages are JSON frames:

```json
// Connector → Gateway
{"type": "message", "connector": "my_bot", "chat_id": "123", "text": "hello", "user_id": "456"}

// Gateway → Connector (reply)
{"type": "reply", "connector": "my_bot", "chat_id": "123", "text": "Hello back"}

// Gateway → Connector (approval request)
{"type": "approval_request", "connector": "control_panel_bot", "chat_id": "789", "text": "⚠️ Confirm shell.run ..."}
```

---

## Running Connectors Individually

```bash
# Telegram bot connector
.venv/bin/umabot channels telegram --mode channel

# Telegram as control panel
.venv/bin/umabot channels telegram --mode control

# Telegram user account (MTProto — reads all personal chats)
.venv/bin/umabot channels telegram-user --connector my_account

# First-time login (interactive phone/OTP flow)
.venv/bin/umabot channels telegram-user --connector my_account --login
```

Set `connectors[].allow_login: true` in config for the first run when using `make start`.

---

## Deployment

### systemd (Linux)

```ini
[Unit]
Description=UmaBot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/umabot
ExecStart=/opt/umabot/.venv/bin/umabot start
ExecStop=/opt/umabot/.venv/bin/umabot stop
ExecReload=/opt/umabot/.venv/bin/umabot reload
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### launchd (macOS)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>        <string>com.umabot.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/umabot/.venv/bin/umabot</string>
    <string>start</string>
  </array>
  <key>RunAtLoad</key>    <true/>
  <key>KeepAlive</key>    <true/>
</dict>
</plist>
```

---

## Process Signals

| Signal | Effect |
|--------|--------|
| `SIGTERM` | Graceful shutdown — drain queue, close connectors |
| `SIGHUP` | Hot reload — re-read `config.yaml`, re-scan skill dirs |

---

## Storage Schema (SQLite)

| Table | Contents |
|-------|---------|
| `messages` | Full message history per chat |
| `sessions` | Per-chat LLM context window state |
| `tasks` | Scheduled task definitions |
| `task_runs` | Execution history and results |
| `audit_log` | Tool calls, approvals, and denials |
