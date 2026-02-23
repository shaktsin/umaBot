# UMA BOT (umabot)

Self-hosted personal AI assistant that runs as a long-running daemon and is controlled via chat channels.

## Highlights
- Control plane gateway with channel adapters (Telegram + Discord), worker queue, skills, policy engine, tools, and storage.
- Asyncio + SQLite durable queue.
- Hot reload via `umabot reload` (SIGHUP).
- Skills with `SKILL.md` frontmatter and strict tool allowlists.
- Tool risk tiers with explicit confirmation for RED actions.

## Quick Start
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

umabot init
umabot start
umabot status
```

## CLI
```bash
umabot init
umabot start
umabot stop
umabot status
umabot reload
umabot skills list
umabot skills install <path>
umabot skills configure <name>
umabot skills remove <name>
umabot skills lint
umabot tasks create --name "Daily Todos" --prompt "Summarize my todos" --type periodic --frequency daily --time 09:00 --timezone UTC
umabot tasks list
umabot tasks cancel 1
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
- `RED` requires confirmation: `Reply YES <token> to confirm`.
- Shell tool is disabled by default.
 - Confirmations can be routed to a control channel via `runtime.control_channel` and `runtime.control_chat_id`.

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
