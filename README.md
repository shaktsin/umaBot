# UMA BOT (umabot)

Self-hosted personal AI assistant that runs as a long-running daemon and is controlled via chat channels.

## Highlights
- Control plane gateway with channel adapters (Telegram + Discord), worker queue, skills, policy engine, tools, and storage.
- Asyncio + SQLite durable queue.
- Hot reload via `umabot reload` (SIGHUP).
- Skills with `SKILL.md` frontmatter and strict tool allowlists.
- Tool risk tiers with explicit confirmation for RED actions.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            USER INTERFACES                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  👤 Owner          👥 External Users                                        │
│  (Control Panel)   (Telegram/Discord/WhatsApp)                             │
│        │                    │                                               │
│        └────────┬───────────┘                                               │
│                 │                                                           │
└─────────────────┼───────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      CONNECTOR LAYER (Out-of-Process)                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐                   │
│  │ Control  │  │ Telegram │  │ Telegram │  │ Discord  │                   │
│  │  Panel   │  │   Bot    │  │   User   │  │   Bot    │                   │
│  │Connector │  │Connector │  │Connector │  │Connector │                   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘                   │
│       │             │               │             │                         │
│       └─────────────┴───────────────┴─────────────┘                         │
│                          │                                                  │
│                          ▼                                                  │
│                   ┌─────────────┐                                           │
│                   │ WebSocket   │                                           │
│                   │ Hub :8765   │ (Routes msgs between connectors/gateway) │
│                   └──────┬──────┘                                           │
│                          │                                                  │
└──────────────────────────┼──────────────────────────────────────────────────┘
                           │ WebSocket (ws://)
                           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          GATEWAY PROCESS                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌────────────────┐         ┌──────────────┐        ┌─────────────────┐   │
│  │   WebSocket    │────────▶│   Message    │───────▶│  Control Panel  │   │
│  │    Gateway     │         │    Router    │        │     Manager     │   │
│  └────────────────┘         └──────┬───────┘        └────────┬────────┘   │
│                                    │                         │             │
│                          ┌─────────┴─────────┐              │             │
│                          │                   │              │             │
│                          ▼                   ▼              │             │
│                  Control Messages    External Messages      │             │
│                          │                   │              │             │
│                          │                   ▼              │             │
│                          │            ┌─────────────┐       │             │
│                          │            │   Message   │       │             │
│                          │            │    Queue    │       │             │
│                          │            │  (SQLite)   │       │             │
│                          │            └──────┬──────┘       │             │
│                          │                   │              │             │
└──────────────────────────┼───────────────────┼──────────────┼─────────────┘
                           │                   │              │
                           │                   ▼              │
                           │       ┌────────────────────┐     │
                           │       │  WORKER PROCESS    │     │
                           │       ├────────────────────┤     │
                           │       │                    │     │
                           │       │  ┌──────────────┐  │     │
                           │       │  │    Worker    │  │     │
                           │       │  │  Event Loop  │◀─┼─────┘ (RED tool confirm)
                           │       │  └──────┬───────┘  │
                           │       │         │          │
                           │       │         ▼          │
                           │       │  ┌──────────────┐  │
                           │       │  │  LLM Client  │  │
                           │       │  │ OpenAI/Claude│  │
                           │       │  │   /Gemini    │  │
                           │       │  └──────┬───────┘  │
                           │       │         │          │
                           │       │         ▼          │
                           │       │  ┌──────────────┐  │
                           │       │  │Policy Engine │  │
                           │       │  │ Risk: 🟢🟡🔴 │  │
                           │       │  └──────┬───────┘  │
                           │       │         │          │
                           │       │         ▼          │
                           │       │  ┌──────────────┐  │
                           │       │  │  Unified     │  │
                           │       │  │    Tool      │  │
                           │       │  │  Registry    │  │
                           │       │  └──────┬───────┘  │
                           │       │         │          │
                           │       └─────────┼──────────┘
                           │                 │
                           │                 ▼
                           │    ┌────────────┴────────────┐
                           │    │                         │
                           │    ▼                         ▼
┌──────────────────────────┼────────────┐    ┌────────────────────┐
│       TOOL SYSTEM        │            │    │   TASK SCHEDULER   │
├──────────────────────────┴────────────┤    ├────────────────────┤
│                                       │    │                    │
│  ┌──────────┐  ┌──────────┐  ┌──────┐│    │  ┌──────────────┐  │
│  │ Built-in │  │ Skills   │  │ MCP  ││    │  │   Cron +     │  │
│  │  Tools   │  │ Python/  │  │JSON- ││    │  │  One-time    │  │
│  │  🟢      │  │ Bash     │  │ RPC  ││    │  │    Tasks     │  │
│  │shell.run │  │  🟡      │  │  🔵  ││    │  └──────┬───────┘  │
│  └──────────┘  └────┬─────┘  └──────┘│    │         │          │
│                     │                 │    └─────────┼──────────┘
│                     ▼                 │              │
│              ┌─────────────┐          │              │
│              │ Subprocess  │          │              │
│              │  .venv      │          │              │
│              │ isolation   │          │              │
│              └─────────────┘          │              │
└────────────────────────────────────────┘              │
                                                        │
                    ┌───────────────────────────────────┘
                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           STORAGE LAYER                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────┐  ┌────────────────┐  ┌──────────────────────────┐   │
│  │   SQLite DB      │  │  Vault Dir     │  │    Skills Directory      │   │
│  │                  │  │                │  │                          │   │
│  │ • messages       │  │ • file storage │  │ • ~/.umabot/skills/      │   │
│  │ • sessions       │  │ • sensitive    │  │ • ./skills/              │   │
│  │ • tasks          │  │   data         │  │                          │   │
│  │ • task_runs      │  │                │  │ Each skill has:          │   │
│  │ • audit_log      │  │                │  │  - SKILL.md (manifest)   │   │
│  │                  │  │                │  │  - .venv (isolated deps) │   │
│  │                  │  │                │  │  - scripts/ (Python/Bash)│   │
│  └──────────────────┘  └────────────────┘  └──────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Purpose | Process |
|-----------|---------|---------|
| **Control Panel** | Owner's private UI for confirmations | Separate connector |
| **Connectors** | Message sources (Telegram, Discord, etc.) | Out-of-process workers |
| **WebSocket Hub** | Routes messages between connectors and gateway | Gateway subprocess |
| **Message Router** | Classifies control vs external messages | Gateway main |
| **Worker** | Processes messages with LLM + tools | Async event loop |
| **Unified Tool Registry** | Manages built-in tools, skills, and MCP | Worker component |
| **Policy Engine** | Risk assessment and confirmation flow | Worker component |
| **Task Scheduler** | Executes periodic and one-time tasks | Separate event loop |
| **SQLite DB** | Persistent storage for messages, tasks, audit | Shared across components |

### Message Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      MESSAGE PROCESSING FLOW                            │
└─────────────────────────────────────────────────────────────────────────┘

  User sends message
         │
         ├──▶ [1] Connector receives (Telegram/Discord/WhatsApp)
         │
         ├──▶ [2] WebSocket connection → Gateway
         │
         ├──▶ [3] Message Router classifies:
         │         ├─ Control message? → Control Panel Manager
         │         └─ External message? → Message Queue
         │
         ├──▶ [4] Queue persists to SQLite
         │
         ├──▶ [5] Worker claims job from queue
         │
         ├──▶ [6] Worker builds context (refresh skills, load history)
         │
         ├──▶ [7] LLM processes message + available tools
         │         │
         │         ├─ LLM decides: needs tool call?
         │         │         │
         │         │         ├─ YES → [8] Policy Engine checks risk
         │         │         │            │
         │         │         │            ├─ 🟢 GREEN: Auto-approve
         │         │         │            ├─ 🟡 YELLOW: Auto-approve
         │         │         │            └─ 🔴 RED: Request confirmation
         │         │         │                     │
         │         │         │                     └──▶ Control Panel
         │         │         │                           Owner approves/denies
         │         │         │
         │         │         └─ [9] Execute tool:
         │         │                 ├─ Built-in: Run in-process
         │         │                 ├─ Skill: Spawn subprocess (venv)
         │         │                 └─ MCP: Call external API
         │         │
         │         └─ [10] LLM generates final response with tool results
         │
         ├──▶ [11] Response sent to WebSocket Hub
         │
         ├──▶ [12] Hub routes to original connector
         │
         └──▶ [13] User receives reply

┌─────────────────────────────────────────────────────────────────────────┐
│  Example: User asks "What files are in my home directory?"              │
│                                                                          │
│  1. Telegram connector receives message                                 │
│  2. Gateway routes as external message                                  │
│  3. Worker claims, sends to LLM with tool list                          │
│  4. LLM decides to call: shell.run("ls ~")                              │
│  5. Policy checks: shell.run is 🔴 RED                                  │
│  6. Control Panel asks owner: "Confirm shell.run: ls ~?"                │
│  7. Owner replies: "YES token123"                                       │
│  8. Tool executes: subprocess runs "ls ~"                               │
│  9. LLM formats result: "Your home directory contains: ..."             │
│  10. Response sent back to user via Telegram                            │
└─────────────────────────────────────────────────────────────────────────┘
```

### Tool Execution Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      TOOL ROUTING & EXECUTION                           │
└─────────────────────────────────────────────────────────────────────────┘

  LLM decides to call tool: "tool_name"
         │
         ▼
  UnifiedToolRegistry.execute_tool(name, args)
         │
         │ (Route based on tool name prefix)
         │
         ├─────────────┬─────────────┬─────────────┐
         │             │             │             │
         ▼             ▼             ▼             ▼
  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │ Built-in │  │  Skill   │  │  Skill   │  │   MCP    │
  │  Tools   │  │ (Python) │  │  (Bash)  │  │  Server  │
  │   🟢     │  │   🟡     │  │   🟡     │  │   🔵     │
  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
       │             │              │              │
       │             │              │              │
  ┌────▼─────────────▼──────────────▼──────────────▼────┐
  │                                                      │
  │  shell.run      skill_github_   skill_backup_      mcp_filesystem_
  │                 create_pr       run                 read_file
  │                                                      │
  └──────┬───────────┬──────────────┬───────────────────┬┘
         │           │              │                   │
         ▼           ▼              ▼                   ▼
  ┌──────────┐  ┌─────────┐  ┌──────────┐  ┌──────────────────┐
  │ Execute  │  │ Spawn   │  │ Spawn    │  │ HTTP/JSON-RPC    │
  │in-process│  │ python  │  │ bash     │  │ to external      │
  │          │  │ venv/   │  │ script   │  │ MCP server       │
  │async fn()│  │ bin/    │  │          │  │                  │
  │          │  │ python  │  │ + stdin  │  │ {"method": ...}  │
  │          │  │ script  │  │   JSON   │  │                  │
  └──────────┘  └─────────┘  └──────────┘  └──────────────────┘
       │             │              │                   │
       └─────────────┴──────────────┴───────────────────┘
                              │
                              ▼
                       ToolResult
                    (content, data)
                              │
                              ▼
                      Back to Worker
                              │
                              ▼
                      Sent to LLM for
                      final response

┌─────────────────────────────────────────────────────────────────────────┐
│  Tool Examples:                                                          │
│                                                                          │
│  shell.run            → Built-in tool (subprocess.run)                  │
│  skill_github_pr      → Skill tool (Python script in venv)              │
│  skill_backup_run     → Skill tool (Bash script)                        │
│  mcp_fs_read_file     → MCP tool (external filesystem server)           │
│                                                                          │
│  Tool Naming Convention:                                                │
│  • Built-in: <category>.<action>         (e.g., shell.run)              │
│  • Skills:   skill_<name>_<script>       (e.g., skill_github_create_pr) │
│  • MCP:      mcp_<server>_<tool>         (e.g., mcp_github_create_issue)│
└─────────────────────────────────────────────────────────────────────────┘
```

### Security Layers

| Layer | Protection |
|-------|-----------|
| **Input Validation** | JSON Schema for all tool calls |
| **Risk Assessment** | 🟢 GREEN (safe) / 🟡 YELLOW (caution) / 🔴 RED (confirm) |
| **Isolation** | Skills run in separate virtualenv subprocesses |
| **Confirmation** | RED tools require owner approval via control panel |
| **Resource Limits** | CPU/memory/timeout limits on skill execution |

## Control Panel Setup

The control panel is your **private interface** for:
- Receiving confirmations for 🔴 RED tools (like shell.run)
- Getting task execution results
- System notifications

### Automatic Setup (Recommended)

```bash
umabot control-panel setup
```

**What it does:**
1. Asks for your Telegram bot token (from @BotFather)
2. Verifies the bot works
3. **Automatically captures your chat ID** when you send a message
4. Saves everything to config.yaml
5. Sends confirmation to your Telegram

**Step-by-step:**
```
$ umabot control-panel setup

┌─────────────────────────────────────────────┐
│  Telegram Control Panel Setup              │
└─────────────────────────────────────────────┘

Step 1: Telegram Bot Token
👉 Open Telegram and message @BotFather
👉 Send: /newbot
👉 Follow instructions and copy the token

Enter your bot token: 1234567890:ABC...

✓ Bot verified: @my_uma_bot

Step 2: Send a message to your bot
👉 Open Telegram and search for: @my_uma_bot
👉 Send any message (like /start) to the bot

⠋ Waiting for your message...

✓ Received message from: John
✓ Chat ID: 123456789
✓ Sent confirmation to your Telegram

Step 3: Saving configuration...
✓ Configuration saved to: config.yaml

┌─────────────────────────────────────────────┐
│  ✓ Setup Complete!                          │
│                                             │
│  Your control panel is ready:               │
│    • Bot: @my_uma_bot                       │
│    • Chat ID: 123456789                     │
│    • Connector: control_panel_bot           │
│                                             │
│  Next steps:                                │
│    1. Start UmaBot: umabot start            │
│    2. The bot will use this chat for        │
│       confirmations                         │
│    3. Test it by triggering a 🔴 RED tool   │
└─────────────────────────────────────────────┘
```

### Manual Setup (Alternative)

If you prefer to configure manually:

```yaml
control_panel:
  enabled: true
  ui_type: telegram
  connector: control_panel_bot
  chat_id: "123456789"  # Your Telegram chat ID

connectors:
  - name: control_panel_bot
    type: telegram_bot
    token: "1234567890:ABC..."  # Your bot token
```

**Finding your chat ID manually:**
1. Message @userinfobot on Telegram
2. It will reply with your chat ID
3. Copy the ID to config.yaml

## Quick Start
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

umabot init                    # Configure UmaBot
umabot control-panel setup     # Set up Telegram control panel (automatic chat ID)
umabot start                   # Start daemon
umabot status                  # Check status
```

## CLI
```bash
# Setup and daemon management
umabot init                              # Interactive configuration wizard
umabot control-panel setup               # Set up Telegram control panel (auto chat ID)
umabot start                             # Start daemon
umabot stop                              # Stop daemon
umabot status                            # Show daemon status
umabot reload                            # Reload configuration

# Skill management
umabot skills list                       # List installed skills
umabot skills install <source>           # Install from PyPI/GitHub/local
umabot skills remove <name>              # Remove skill
umabot skills lint                       # Validate skills

# Task scheduling
umabot tasks create --name "Daily Todos" --prompt "Summarize my todos" --type periodic --frequency daily --time 09:00 --timezone UTC
umabot tasks list                        # List all tasks
umabot tasks cancel 1                    # Cancel task by ID
```

## Configuration
Precedence: `CLI flags > ENV vars > config.yaml > defaults`.

### Two Ways to Configure
1. Run `umabot init` to answer prompts and write `config.yaml`.
2. Provide a `config.yaml` file (copy from `config.example.yaml`).

### Supported Files
- `config.yaml`
- `config.example.yaml`

### CLI Overrides (Optional)
Use `--set` with either `section.field=value` or `UMABOT_ENV=value`:
```bash
umabot start --set llm.provider=openai --set llm.model=gpt-4o-mini --set UMABOT_LLM_API_KEY=YOUR_KEY
```

### Key Environment Variables
- `UMABOT_LLM_PROVIDER`
- `UMABOT_LLM_MODEL`
- `UMABOT_LLM_API_KEY`
- `UMABOT_TELEGRAM_TOKEN`, `UMABOT_TELEGRAM_ENABLED`
- `UMABOT_DISCORD_TOKEN`, `UMABOT_DISCORD_ENABLED`
- `UMABOT_WHATSAPP_TOKEN`, `UMABOT_WHATSAPP_ENABLED`
- `UMABOT_SHELL_TOOL`
- `UMABOT_CONFIRMATION_STRICTNESS`
- `UMABOT_DB_PATH`
- `UMABOT_VAULT_DIR`
- `UMABOT_PID_FILE`
- `UMABOT_LOG_DIR`
- `UMABOT_CONTROL_CONNECTOR`
- `UMABOT_WS_HOST`
- `UMABOT_WS_PORT`
- `UMABOT_WS_TOKEN`

### Example `config.yaml`
```yaml
llm:
  provider: openai
  model: gpt-4o-mini
telegram:
  enabled: true
  token:

discord:
  enabled: false
  token:

whatsapp:
  enabled: false
  token:

connectors:
  - name: telegram_control
    type: telegram_bot
    token:
  - name: telegram_user
    type: telegram_user
    api_id:
    api_hash:
    session_name:
    phone:
    allow_login: false

tools:
  shell_enabled: false

policy:
  confirmation_strictness: normal

storage:
  db_path: ~/.umabot/umabot.db
  vault_dir: ~/.umabot/vault

runtime:
  pid_file: ~/.umabot/umabot.pid
  log_dir: ~/.umabot/logs
  control_channel: telegram
  control_chat_id:
  control_connector:
  ws_host: 127.0.0.1
  ws_port: 8765
  ws_token:
```

## Channels
- **Telegram**: enabled via `telegram.enabled` and `telegram.token`.
- **Discord**: optional dependency: `pip install -e .[discord]`.
- **WhatsApp**: stub adapter (disabled by default).

## Skills
Skills are folders with `SKILL.md` containing YAML frontmatter:

```yaml
---
name: daily_planner
version: 1.0.0
description: Creates daily plans and tasks
allowed_tools:
  - skills.run_script
risk_level: yellow
triggers:
  - "plan my day"
scripts:
  run: scripts/run.py
install_config:
  args:
    data_file:
      type: string
      required: true
      default: "~/.umabot/vault/data.json"
  env:
    API_TOKEN:
      required: false
      secret: true
runtime:
  timeout_seconds: 20
---
```

Loaded from:
- `./skills`
- `~/.umabot/skills`

Rules:
- Skills cannot define new tools.
- Tools must be explicitly allowlisted.
- Scripted skills run in isolated per-skill virtualenv subprocesses.
- Skill install-time `args/env` are persisted under `skill_configs` in `config.yaml`.

## Tool Security
- JSON schema validation for all tool calls.
- Risk tiers: `GREEN`, `YELLOW`, `RED`.
- `RED` requires confirmation: `Reply YES <16-char-token> to confirm` (128-bit entropy).
- Shell tool is disabled by default.
- Confirmations can be routed to a control channel via `runtime.control_channel` and `runtime.control_chat_id`.

## Security Best Practices

### Secret Management
**⚠️ IMPORTANT: Never commit secrets to git!**

UmaBot supports multiple ways to store secrets securely:

1. **Environment Variables (Recommended for Production)**
   ```bash
   # For LLM API key
   export UMABOT_LLM_API_KEY="your-api-key"

   # For connector tokens (replace CONNECTOR_NAME with your connector name)
   export UMABOT_CONNECTOR_CONTROL_PANEL_BOT_TOKEN="your-telegram-token"
   export UMABOT_CONNECTOR_PUBLIC_TELEGRAM_TOKEN="your-other-token"
   ```

2. **macOS Keychain (Automatic on macOS)**
   - Secrets are automatically stored in macOS Keychain during `umabot init`
   - Retrieved securely when UmaBot starts
   - Never stored in plaintext config files

3. **~/.umabot/.env File (Fallback on Linux)**
   - Used when Keychain is unavailable
   - File permissions automatically set to `0600` (user-only)
   - Directory permissions set to `0700` (user-only access)

### Config File Security
- `config.yaml` is automatically excluded from git (see `.gitignore`)
- API keys and tokens are **stripped** before saving to config
- Tokens must be provided via environment variables or keychain
- Session files (`.session`) are also git-ignored

### Logging Security
- Secrets are never logged in plaintext
- Tokens are masked: `***<last-4-chars>` in debug logs
- Confirmation tokens are hashed before logging (SHA256, 8-char prefix)

### Production Deployment Checklist
- [ ] Use environment variables for all secrets
- [ ] Set restrictive file permissions on config directory (`chmod 700 ~/.umabot`)
- [ ] Enable shell tool only if absolutely necessary
- [ ] Review allowed tools in skill configurations
- [ ] Use separate control panel bot token (not your personal account)
- [ ] Regularly rotate API keys and tokens
- [ ] Monitor logs for unauthorized access attempts

## Message Router
UMA BOT distinguishes between:
- **Control messages**: from the owner control channel/chat id.
- **External messages**: from other platforms/users.

Control messages are used for owner interaction and confirmations. External messages are processed and replied to on their original channel.

### Runtime Flow
1. Channel adapters receive messages (webhook or polling) and forward to the Gateway.
2. The Message Router classifies each message as control or external.
3. The Worker processes the message using skills, policy, and tools.
4. Responses go back to the original channel; confirmations go to the control channel.

## Daemon
`umabot start` runs the orchestrator (gateway + connectors) in the background and writes a PID file.
Log level can be set via `--log-level` or `UMABOT_LOG_LEVEL` (e.g., `DEBUG`).

## Orchestrator
Run gateway and all configured connectors in one command:
```bash
umabot orchestrate --log-level DEBUG
```

## WebSocket Channel Workers
Gateway exposes a WebSocket endpoint for channel workers. Set `runtime.ws_token` in config and run workers as separate processes.

### Telegram Worker (channel mode)
```bash
umabot channels telegram --mode channel
```

### Telegram Worker (control mode)
```bash
umabot channels telegram --mode control
```

Control mode is a separate long-lived connection used for owner confirmations. Configure `runtime.control_channel=telegram`, `runtime.control_chat_id` and (optionally) `runtime.control_connector`.

### Telegram User Connector (reads all user chats/channels)
```bash
umabot channels telegram-user --connector telegram_user
```

First-time login (interactive):
```bash
umabot channels telegram-user --connector telegram_user --login
```

When using `umabot orchestrate`, set `connectors[].allow_login: true` for the first run to complete auth.

### systemd example
```ini
[Unit]
Description=UMA BOT
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/umabot
ExecStart=/path/to/umabot/.venv/bin/umabot start
ExecStop=/path/to/umabot/.venv/bin/umabot stop
ExecReload=/path/to/umabot/.venv/bin/umabot reload
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### launchd example (macOS)
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.umabot.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/umabot/.venv/bin/umabot</string>
    <string>start</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
```

## Tasks
- One-time and periodic tasks are stored in SQLite (`tasks`, `task_runs`).
- Tasks can be created from control chat messages:
 - `task daily 09:00 summarize my todos`
 - `task weekly mon 09:00 summarize my inbox`
 - `task once 2026-03-01T10:00:00 prepare meeting brief`
 - `tasks list`
 - `tasks cancel 3`
- The scheduler enqueues due tasks and the worker runs them through the LLM.
- Task results are sent to the configured control panel.

## Notes
- The daemon responds to `SIGTERM` for graceful shutdown and `SIGHUP` for reload.
- `vault_dir` is retained for future file tools.
