# UmaBot Architecture

Reference for contributors and advanced users. Covers internals, message flow, tool routing, deployment, and extension points.

---

## System Overview

```
 LISTENER CONNECTORS  (inbound-only, out-of-process)
 ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐
 │  Gmail IMAP  │  │  Telegram User   │  │  Discord / …     │
 │  (IMAP IDLE) │  │  (MTProto)       │  │                  │
 └──────┬───────┘  └──────┬───────────┘  └──────┬───────────┘
        └─────────────────┴──── WS ──────────────┘
                                │
                      ┌─────────▼─────────┐
                      │   PII Filter       │  email, phone, SSN masked
                      └─────────┬─────────┘
                                │
 ADMIN CONNECTORS  (bidirectional, out-of-process)
 ┌────────────────────┐  ┌──────────────────────────┐
 │  Web Panel (local) │  │  Telegram Bot (remote)   │
 │  127.0.0.1:8080    │  │  control_panel_bot       │
 └──────────┬─────────┘  └──────────────┬────────────┘
            └─────────────── WS ─────────┘
                                │
                      ┌─────────▼──────────────────────────────────┐
                      │              GATEWAY  :8765                 │
                      │                                             │
                      │  1. Identify connector role (listener|admin)│
                      │  2. Apply PII filter for listener messages  │
                      │  3. Pin listener messages → admin session   │
                      │  4. Persist to DB                           │
                      │  5. Enqueue to LLM Scheduler (P0/P1/P2)    │
                      └─────────────────────┬───────────────────────┘
                                            │
                      ┌─────────────────────▼───────────────────────┐
                      │           LLM SCHEDULER                      │
                      │                                             │
                      │  P0 = admin message    (no delay)           │
                      │  P1 = agent tool-loop  (~1 s)               │
                      │  P2 = listener event   (≥ 60 s gap)         │
                      │  Token bucket + 429 retry-after             │
                      └─────────────────────┬───────────────────────┘
                                            │
                      ┌─────────────────────▼───────────────────────┐
                      │           LLM AGENT  (orchestrator)          │
                      │                                             │
                      │  • AGENT.md system prompt                   │
                      │  • Intent detection  (P2 for listener msgs) │
                      │      → importance / needs_admin / action    │
                      │  • Built-in tools + Skills + MCP            │
                      │  • Policy Engine  🟢🟡🔴                    │
                      │  • Task Scheduler (cron + one-time)         │
                      └───────────────┬──────────────┬──────────────┘
                                      │              │
                          needs_admin │              │ auto-action
                                      ▼              ▼
                      ALL admin panels broadcast   Task Store (DB)
                      Web Panel + Telegram Bot     silent queue
                      simultaneously
                                      │
                          Admin replies → gateway
                                      │
                      ┌───────────────▼──────────────────────────────┐
                      │           REPLY ROUTING                       │
                      │                                              │
                      │  source_connector = gmail_imap               │
                      │    → gmail.send tool (OAuth2)                │
                      │  source_connector = telegram_user            │
                      │    → telegram.send_message(source_chat_id)   │
                      │  source_connector = discord                  │
                      │    → discord.send(source_chat_id)            │
                      └──────────────────────────────────────────────┘

 STORAGE LAYER
 ┌─────────────────────────────────────────────────────────┐
 │  SQLite DB            Vault Dir          Skills Dirs     │
 │  messages             oauth tokens       SKILL.md        │
 │  sessions             sensitive files    per-skill .venv │
 │  tasks / task_runs                       scripts/        │
 │  audit_log                                               │
 └─────────────────────────────────────────────────────────┘
```

---

## Key Components

| Component | Purpose | Where it runs |
|-----------|---------|--------------|
| **Listener connectors** | Inbound-only channels (gmail_imap, telegram_user, discord) | Out-of-process |
| **Admin connectors** | Bidirectional owner interfaces (web panel, telegram_bot) | Out-of-process |
| **PII Filter** | Masks email/phone/SSN/IBAN in listener messages before storage | Gateway |
| **WebSocket Hub** | Routes messages between connectors and gateway | Inside gateway |
| **Message Router** | Classifies messages as control vs external; assigns connector role | Gateway |
| **Control Panel Manager** | Broadcasts notifications to ALL enabled admin panels | Gateway |
| **Message Queue** | SQLite-backed durable job queue | Shared |
| **LLM Scheduler** | Priority queue (P0/P1/P2) + token bucket + 429 retry | Worker |
| **Worker** | Claims jobs, runs intent detection + LLM + tool loop | Async event loop |
| **Intent Detector** | Lightweight P2 LLM call to classify listener messages | Worker |
| **Policy Engine** | Assigns risk tier, manages confirmation tokens | Worker |
| **Unified Tool Registry** | Routes tool calls to built-ins, skills, or MCP | Worker |
| **Task Scheduler** | Enqueues periodic and one-time tasks | Separate asyncio loop |
| **Skill Registry** | Loads and indexes SKILL.md manifests | Startup + hot-reload |

---

## Connector Roles: Listener vs Admin

Every connector is automatically assigned a role based on its `type`.  The role
is never set manually in `config.yaml` — it is derived by `ConnectorConfig.__post_init__`.

| Role | Types | Behaviour |
|------|-------|-----------|
| **listener** | `gmail_imap`, `telegram_user`, `discord` | Inbound-only. PII filtered. Messages pinned to admin session. No direct replies. |
| **admin** | `telegram_bot`, `web` (control panel) | Bidirectional. Trusted. Receives all notifications. Admin replies are processed here. |

### Listener message lifecycle

```
Listener connector sends WS event
        │
        ▼
Gateway: connector_role = get_connector_role(config, connector)  → "listener"
        │
        ├─ filter_pii(message.text)         ← mask email/phone/SSN before DB
        ├─ session pinned to admin session  ← chat_id="admin", channel="web"
        ├─ source_connector + source_chat_id preserved in queue payload
        │
        ▼
Worker: detect_intent(text, llm_client, priority=P2)
        │  → IntentResult(importance, needs_admin, suggested_action, summary)
        │
        ├─ should_skip (ignore + low)?  → discard silently, no LLM cost
        │
        ├─ prepend intent_context_block to LLM messages
        │    [Intent detection]
        │    Importance:       high
        │    Suggested action: draft_reply
        │    Source connector: gmail_imap
        │    To reply: use the gmail.send tool
        │
        ▼
LLM agent runs (full orchestrator or direct)
        │
        ▼
_notify(connector_role="listener") → send_control_message()
        │                              → ALL admin panels simultaneously
        ▼
Admin sees summary + draft reply on web panel AND Telegram bot
Admin replies → LLM uses gmail.send / telegram.send_message / discord.send
```

---

## Message Flow (Admin connector)

```
Admin / user sends message
      │
      ├─ [1]  Connector receives (Telegram long-poll / web panel WS)
      ├─ [2]  Sent over WebSocket to Gateway Hub
      ├─ [3]  connector_role = "admin" → normal session (no pinning, no PII filter)
      ├─ [4]  Message Router classifies: control | external
      ├─ [5]  Enqueued with connector_role="admin", P1 priority
      ├─ [6]  Worker claims job
      ├─ [7]  Worker detects workspace; sets active workspace
      ├─ [8]  LLM receives message + AGENT.md + skills catalog
      │
      │  LLM tool loop:
      ├─ [9]  LLM requests a tool call
      ├─ [10] Policy Engine checks risk tier
      │          🟢 GREEN  → auto-approve
      │          🟡 YELLOW → auto-approve (strict mode: approval required)
      │          🔴 RED    → approval request to ALL admin panels; block until response
      ├─ [11] Tool executes (in-process / subprocess / JSON-RPC)
      ├─ [12] Result returned to LLM; loop continues until no more tool calls
      │
      ├─ [13] LLM generates final response
      ├─ [14] _notify(connector_role="admin") → send_message() → originating connector
      └─ [15] Admin receives reply
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

## LLM Scheduler

All LLM calls go through `LLMScheduler` (`umabot/llm/scheduler.py`), which wraps any provider client with a priority queue and rate limiter.

```
Priority levels:
  P0 = 0  Admin message / urgent command       → no delay, preempts queue
  P1 = 1  Agent tool-loop iteration (default)  → normal, ~1 s
  P2 = 2  Background / listener event          → min 60 s between calls

Token bucket:  sliding 60-second window (tokens_per_minute in config)
429 / 529:     exponential backoff with retry-after header respected
```

Three separate schedulers run concurrently — one for `llm_client`, one for `orchestrator_llm`, one for `agent_llm` — each wrapping their own provider client.  All are started in `Worker.start()` and stopped in `Worker.stop()`.

---

## Intent Detection

When a message arrives from a **listener connector**, the worker runs a lightweight P2 LLM call before the full agent loop:

```python
intent = await detect_intent(text, llm_client)   # P2 — cheap, fast
# IntentResult fields:
#   importance:        "high" | "medium" | "low"
#   needs_admin:       True | False
#   suggested_action:  "summarize" | "draft_reply" | "create_task" | "ignore"
#   summary:           "1-2 sentence plain English summary"

if intent.should_skip:   # ignore + low → no LLM cost, silent discard
    return

# Otherwise prepend intent context to agent conversation:
# [Intent detection]
# Importance:       high
# Suggested action: draft_reply
# Source connector: gmail_imap
# To reply: use the gmail.send tool (include the original Subject as Re: <Subject>)
```

Fallback: if intent detection itself fails, `_FALLBACK` is used (`medium` / `needs_admin=True` / `summarize`) so the message always reaches the admin.

---

## Security Layers

| Layer | Mechanism |
|-------|----------|
| **PII filter** | Email / phone / SSN / IBAN masked in listener messages before DB storage |
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
