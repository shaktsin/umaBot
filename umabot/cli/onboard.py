"""Interactive onboarding wizard for UMA BOT."""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import Optional

import qrcode
import questionary
from questionary import Choice
from rich.console import Console
from rich.panel import Panel
from telethon import TelegramClient
from telethon.sessions import StringSession

from umabot.config.schema import (
    Config,
    ConnectorConfig,
    WorkspaceACL,
    WorkspaceConfig,
    default_config,
)
from umabot.llm.openai_client import _is_reasoning_model
from umabot.config.loader import load_config, save_config as save_cfg, store_secrets
from umabot.storage import Database

console = Console()


def run_wizard(
    config_path: Optional[str] = None,
    install_daemon: bool = False,
    reset: bool = False,
) -> str:
    """Run interactive onboarding wizard — additive on re-runs.

    Fresh install: walks every step in order.
    Existing config: shows current state and lets the user pick which
    steps to run; unselected steps are left exactly as-is.
    """
    config_file = Path(config_path).expanduser() if config_path else Path.home() / ".umabot" / "config.yaml"
    is_update = config_file.exists() and not reset

    if is_update:
        console.print(
            Panel.fit(
                "[bold cyan]UMA BOT Setup[/bold cyan]\n"
                f"Updating existing configuration at [dim]{config_file}[/dim]",
                border_style="cyan",
            )
        )
        cfg, _ = load_config(config_path=str(config_file))
        _print_current_status(cfg)
        selected = _pick_steps(cfg)
        if not selected:
            console.print("[yellow]Nothing selected — no changes made.[/yellow]")
            return str(config_file)
        setup_mode = "quickstart"
    else:
        console.print(
            Panel.fit(
                "[bold cyan]UMA BOT Onboarding Wizard[/bold cyan]\n"
                "Let's set up your personal AI assistant",
                border_style="cyan",
            )
        )
        cfg = default_config()
        selected = {"llm", "control_panel", "connectors", "integrations", "tools", "workspaces", "skills_dirs", "agents"}
        setup_mode = questionary.select(
            "Choose setup mode:",
            choices=[
                Choice("QuickStart (recommended defaults)", value="quickstart"),
                Choice("Advanced (full configuration)", value="advanced"),
            ],
        ).ask() or "quickstart"

    # Tokens to (re-)store in keychain/env — only populated when the user
    # explicitly enters a new value for that secret.
    new_api_key = ""
    new_telegram_token = ""
    new_discord_token = ""

    console.print("[dim]Tip: press Ctrl+C at any time to save progress and exit.[/dim]\n")

    try:
        # ── Step 1: AI Provider ───────────────────────────────────────────
        if "llm" in selected:
            console.print("\n[bold]Step 1: AI Provider[/bold]")
            provider = questionary.select(
                "Select your AI provider:",
                choices=[
                    Choice("OpenAI (GPT-4, GPT-4o)", value="openai"),
                    Choice("Anthropic Claude", value="claude"),
                    Choice("Google Gemini", value="gemini"),
                ],
                default=cfg.llm.provider or "openai",
            ).ask()
            cfg.llm.provider = provider

            models = _get_models_for_provider(provider)
            model = questionary.select(
                f"Select {provider} model:",
                choices=models,
            ).ask()
            cfg.llm.model = model

            if provider == "openai" and _is_reasoning_model(model):
                effort = questionary.select(
                    "Reasoning effort:",
                    choices=[
                        Choice("High — best quality", value="high"),
                        Choice("Medium — balanced (recommended)", value="medium"),
                        Choice("Low — fastest / cheapest", value="low"),
                    ],
                    default=cfg.llm.reasoning_effort or "medium",
                ).ask()
                cfg.llm.reasoning_effort = effort

            key_hint = " [dim](press Enter to keep existing)[/dim]" if is_update else ""
            raw = questionary.password(f"Enter {provider} API key:{key_hint}").ask() or ""
            if raw.strip():
                new_api_key = raw.strip()
                cfg.llm.api_key = new_api_key
            elif is_update:
                console.print("[dim]  → API key unchanged.[/dim]")

        # ── Step 2: Control Panel ─────────────────────────────────────────
        if "control_panel" in selected:
            console.print("\n[bold]Step 2: Control Panel[/bold]")
            console.print(
                "[dim]Your private interface for confirmations and management.\n"
                "You can run multiple control panels (e.g. web + Telegram) simultaneously.[/dim]\n"
            )
            new_tokens: dict = {}
            _step_control_panel(cfg, is_update, new_tokens)
            new_telegram_token = new_tokens.get("telegram", "")
            new_discord_token = new_tokens.get("discord", "")

        # ── Step 3: Integrations ──────────────────────────────────────────
        if "integrations" in selected:
            console.print("\n[bold]Step 3: Integrations[/bold]")
            console.print(
                "[dim]Third-party services the bot acts on (not messaging channels).[/dim]\n"
            )
            _setup_integrations_interactive(cfg)

        # ── Step 4: Message Connectors ────────────────────────────────────
        if "connectors" in selected:
            console.print("\n[bold]Step 4: Message Connectors[/bold]")
            if cfg.connectors:
                names = ", ".join(c.name for c in cfg.connectors)
                console.print(f"[dim]Currently configured: {names}[/dim]")
            console.print("[dim]Add more connectors below — existing ones are kept.[/dim]")
            _setup_connectors_interactive(cfg)

        # ── Step 5: Tools & Security ──────────────────────────────────────
        if "tools" in selected:
            console.print("\n[bold]Step 5: Tools[/bold]")
            current = "[green]enabled[/green]" if cfg.tools.shell_enabled else "[dim]disabled[/dim]"
            cfg.tools.shell_enabled = questionary.confirm(
                f"Enable shell command tool? (currently {current})",
                default=cfg.tools.shell_enabled,
            ).ask()

            if setup_mode == "advanced":
                strictness = questionary.select(
                    "Confirmation strictness:",
                    choices=[
                        Choice("Normal (confirm RED tier only)", value="normal"),
                        Choice("Strict (confirm all tools)", value="strict"),
                    ],
                    default=cfg.policy.confirmation_strictness or "normal",
                ).ask()
                cfg.policy.confirmation_strictness = strictness

        # ── Step 6: Workspaces ────────────────────────────────────────────
        if "workspaces" in selected:
            console.print("\n[bold]Step 6: Workspaces[/bold]")
            console.print(
                "[dim]Named directories umabot can read/write. Each has its own ACL.\n"
                "Agents pick the right workspace per task. No workspace = uses a temp dir.[/dim]\n"
            )
            _step_workspaces(cfg)

        # ── Step 7: Skills ────────────────────────────────────────────────
        if "skills_dirs" in selected:
            console.print("\n[bold]Step 7: Skills[/bold]")
            console.print(
                "[dim]Directories containing Agent Skills (SKILL.md folders).\n"
                "Each directory is scanned recursively for skills at startup.[/dim]\n"
            )
            _step_skill_dirs(cfg)

        # ── Step 8: Agents ────────────────────────────────────────────────
        if "agents" in selected:
            console.print("\n[bold]Step 8: Multi-agent orchestration[/bold]")
            console.print(
                "[dim]When enabled, requests are routed through a dynamic orchestrator\n"
                "that spawns specialist agents with tool access and workspace awareness.[/dim]\n"
            )
            _step_agents(cfg)

    except KeyboardInterrupt:
        console.print("\n[yellow]Wizard interrupted — saving progress so far...[/yellow]")

    # ── Persist ──────────────────────────────────────────────────────────
    # Generate ws_token only if one doesn't already exist
    if not cfg.runtime.ws_token:
        cfg.runtime.ws_token = secrets.token_urlsafe(32)

    # Capture Google client_secret before save_cfg strips it from the YAML
    google_cfg = getattr(getattr(cfg, "integrations", None), "google", None)
    new_google_client_secret = (getattr(google_cfg, "client_secret", "") or "") if google_cfg else ""

    config_file.parent.mkdir(parents=True, exist_ok=True)
    _ensure_agent_context_file(config_file.parent)
    save_cfg(cfg, str(config_file))

    # Only store secrets the user explicitly provided in this run
    store_secrets(
        api_key=new_api_key,
        telegram_token=new_telegram_token,
        discord_token=new_discord_token,
        google_client_secret=new_google_client_secret,
    )

    panel_hint = ""
    if cfg.control_panel.enabled and cfg.control_panel.ui_type == "web":
        panel_hint = (
            f"\n  • Control panel: [link]http://127.0.0.1:{cfg.control_panel.web_port}[/link]"
            " (starts with daemon)"
        )

    agent_md_path = config_file.parent / "AGENT.md"
    console.print(
        Panel.fit(
            f"[bold green]✓ Configuration saved to {config_file}[/bold green]\n\n"
            "Next steps:\n"
            "  • Run [cyan]make doctor[/cyan] to verify setup\n"
            "  • Run [cyan]make run[/cyan] to start in foreground, or [cyan]make start[/cyan] for daemon"
            f"{panel_hint}\n"
            f"  • Edit [cyan]{agent_md_path}[/cyan] to give the bot context about you",
            border_style="green",
        )
    )

    if install_daemon:
        _install_system_daemon(config_file)

    return str(config_file)


# ---------------------------------------------------------------------------
# Wizard helpers
# ---------------------------------------------------------------------------

def _all_active_panels(cfg: Config) -> list:
    """Return deduplicated list of all enabled control panels across both config fields."""
    seen: set = set()
    panels = []
    for p in list(cfg.control_panels) + ([cfg.control_panel] if cfg.control_panel.enabled else []):
        key = (p.ui_type, p.chat_id or "", p.connector or "")
        if key not in seen:
            seen.add(key)
            panels.append(p)
    return panels


def _panel_label(p) -> str:
    if p.ui_type == "web":
        return f"web @ {p.web_port}"
    return p.ui_type


def _print_current_status(cfg: Config) -> None:
    """Print a compact summary of what is already configured."""
    provider_info = f"{cfg.llm.provider} / {cfg.llm.model}" if cfg.llm.provider else "not set"
    active_panels = _all_active_panels(cfg)
    cp_info = ", ".join(_panel_label(p) for p in active_panels) if active_panels else "not configured"
    connector_info = (
        ", ".join(c.name for c in cfg.connectors) if cfg.connectors else "none"
    )
    google_info = (
        "[green]✓ configured[/green]" if cfg.integrations.google.client_id
        else "[dim]not configured[/dim]"
    )
    shell_info = "[green]enabled[/green]" if cfg.tools.shell_enabled else "[dim]disabled[/dim]"
    ws_list = cfg.tools.workspaces or []
    if ws_list:
        ws_info = ", ".join(
            f"{w.name}{'*' if w.default else ''}" for w in ws_list
        ) + f"  ({len(ws_list)} total)"
    else:
        ws_info = "[dim]none — will use tmp[/dim]"

    skill_dirs_info = f"{len(cfg.skill_dirs)} dir(s)" if cfg.skill_dirs else "[dim]none[/dim]"
    agents_info = "[green]enabled[/green]" if cfg.agents.enabled else "[dim]disabled[/dim]"

    console.print(
        "\n[bold]Current configuration[/bold]\n"
        f"  AI Provider    : {provider_info}\n"
        f"  Control Panel  : {cp_info}\n"
        f"  Connectors     : {connector_info}\n"
        f"  Google Workspace: {google_info}\n"
        f"  Shell tool     : {shell_info}\n"
        f"  Workspaces     : {ws_info}\n"
        f"  Skill dirs     : {skill_dirs_info}\n"
        f"  Agents         : {agents_info}\n"
    )


def _pick_steps(cfg: Config) -> set:
    """Let the user pick sections to update via a loop — Enter selects, loop until Done."""
    selected: set = set()

    def _label(step_id: str) -> str:
        """Build a display label showing current value + whether already queued."""
        tag = " [green]✓ queued[/green]" if step_id in selected else ""
        if step_id == "llm":
            info = f"{cfg.llm.provider} / {cfg.llm.model}" if cfg.llm.provider else "not set"
            return f"AI Provider          [{info}]{tag}"
        if step_id == "control_panel":
            active = _all_active_panels(cfg)
            info = ", ".join(_panel_label(p) for p in active) if active else "not configured"
            return f"Control Panel        [{info}]{tag}"
        if step_id == "connectors":
            info = f"{len(cfg.connectors)} configured"
            return f"Message Connectors   [{info}]{tag}"
        if step_id == "integrations":
            info = "Google ✓" if cfg.integrations.google.client_id else "none"
            return f"Integrations         [{info}]{tag}"
        if step_id == "tools":
            info = "shell on" if cfg.tools.shell_enabled else "shell off"
            return f"Tools & Security     [{info}]{tag}"
        if step_id == "workspaces":
            ws_list = cfg.tools.workspaces or []
            info = f"{len(ws_list)} configured" if ws_list else "none"
            return f"Workspaces           [{info}]{tag}"
        if step_id == "skills_dirs":
            info = f"{len(cfg.skill_dirs)} dir(s)" if cfg.skill_dirs else "none"
            return f"Skills               [{info}]{tag}"
        if step_id == "agents":
            info = "enabled" if cfg.agents.enabled else "disabled"
            return f"Agents               [{info}]{tag}"
        return step_id

    _ALL = ["llm", "control_panel", "connectors", "integrations", "tools", "workspaces", "skills_dirs", "agents"]

    while True:
        choices = [Choice(_label(s), value=s) for s in _ALL]
        choices.append(Choice(
            "Done — save and continue" if selected else "Done — no changes",
            value="done",
        ))

        pick = questionary.select(
            "Select a section to configure (Enter to choose, repeat to add more):",
            choices=choices,
        ).ask()

        if pick is None or pick == "done":
            break

        if pick in selected:
            # Toggle off if picked again
            selected.discard(pick)
            console.print(f"[dim]  → {pick} removed from queue.[/dim]")
        else:
            selected.add(pick)
            console.print(f"[green]  → {pick} queued.[/green]")

    return selected


def _workspace_acl_summary(acl) -> str:
    parts = []
    if acl.read:
        parts.append("r")
    if acl.write or acl.create_files:
        parts.append("w")
    if acl.delete_files:
        parts.append("del")
    if acl.shell:
        parts.append("shell")
    return ",".join(parts) if parts else "no-access"


def _step_workspaces(cfg: Config) -> None:
    """Configure named workspaces with per-directory ACL."""
    ws_list = cfg.tools.workspaces or []

    if ws_list:
        console.print("  Currently configured:")
        for ws in ws_list:
            acl_str = _workspace_acl_summary(ws.acl)
            default_tag = "  [cyan][default][/cyan]" if ws.default else ""
            console.print(f"    [bold]{ws.name}[/bold]  {ws.path}  [{acl_str}]{default_tag}")
    else:
        console.print("  [dim]No workspaces — agents will use a temp directory.[/dim]")

    while True:
        action = questionary.select(
            "Workspace action:",
            choices=[
                Choice("Add workspace",    value="add"),
                Choice("Remove workspace", value="remove"),
                Choice("Done",             value="done"),
            ],
        ).ask()

        if action is None or action == "done":
            break

        if action == "add":
            name = (questionary.text("Workspace name (e.g. projects, downloads):").ask() or "").strip()
            if not name:
                continue
            name = name.lower().replace(" ", "-")
            path = (questionary.text(f"Absolute or ~ path for '{name}':").ask() or "").strip()
            if not path:
                continue

            can_read = questionary.confirm("Allow reading files?", default=True).ask()
            can_write = questionary.confirm("Allow writing/modifying files?", default=True).ask()
            can_create = questionary.confirm("Allow creating new files?", default=can_write).ask() if can_write else False
            can_delete = questionary.confirm("Allow deleting files?", default=False).ask() if can_write else False
            can_shell = questionary.confirm("Allow shell commands (cwd)?", default=True).ask()
            is_default = questionary.confirm(
                "Set as default workspace?",
                default=not ws_list,
            ).ask()

            if is_default:
                for existing in ws_list:
                    existing.default = False

            ws = WorkspaceConfig(
                name=name,
                path=path,
                acl=WorkspaceACL(
                    read=can_read,
                    write=can_write,
                    create_files=can_create,
                    delete_files=can_delete,
                    shell=can_shell,
                ),
                default=is_default,
            )
            ws_list.append(ws)
            cfg.tools.workspaces = ws_list
            console.print(f"  [green]✓ Workspace '{name}' added.[/green]")

        elif action == "remove":
            if not ws_list:
                console.print("[yellow]No workspaces to remove.[/yellow]")
                continue
            choices = [Choice(f"{ws.name}  ({ws.path})", value=ws.name) for ws in ws_list]
            choices.append(Choice("Cancel", value=""))
            to_remove = questionary.select("Remove which workspace?", choices=choices).ask()
            if to_remove:
                cfg.tools.workspaces = [w for w in ws_list if w.name != to_remove]
                ws_list = cfg.tools.workspaces
                console.print(f"  [dim]Removed workspace '{to_remove}'.[/dim]")


def _step_skill_dirs(cfg: Config) -> None:
    """Configure directories scanned for Agent Skills."""
    dirs = list(cfg.skill_dirs or [])

    if dirs:
        console.print("  Currently configured:")
        for d in dirs:
            console.print(f"    [cyan]{d}[/cyan]")

    while True:
        action = questionary.select(
            "Skill directory action:",
            choices=[
                Choice("Add directory",    value="add"),
                Choice("Remove directory", value="remove"),
                Choice("Done",             value="done"),
            ],
        ).ask()

        if action is None or action == "done":
            break

        if action == "add":
            path = (questionary.text("Path to skills directory (e.g. ~/projects/skills/skills):").ask() or "").strip()
            if not path:
                continue
            from pathlib import Path as _Path
            expanded = str(_Path(path).expanduser())
            if expanded in dirs:
                console.print(f"  [yellow]Already configured: {expanded}[/yellow]")
                continue
            if not _Path(expanded).exists():
                console.print(f"  [yellow]Warning: path does not exist yet: {expanded}[/yellow]")
            dirs.append(expanded)
            cfg.skill_dirs = dirs
            console.print(f"  [green]✓ Added skill directory: {expanded}[/green]")

        elif action == "remove":
            if not dirs:
                console.print("[yellow]No skill directories to remove.[/yellow]")
                continue
            choices = [Choice(d, value=d) for d in dirs]
            choices.append(Choice("Cancel", value=""))
            to_remove = questionary.select("Remove which directory?", choices=choices).ask()
            if to_remove:
                dirs = [d for d in dirs if d != to_remove]
                cfg.skill_dirs = dirs
                console.print(f"  [dim]Removed: {to_remove}[/dim]")


def _step_agents(cfg: Config) -> None:
    """Configure multi-agent orchestration."""
    current = "[green]enabled[/green]" if cfg.agents.enabled else "[dim]disabled[/dim]"
    cfg.agents.enabled = questionary.confirm(
        f"Enable multi-agent orchestration? (currently {current})",
        default=cfg.agents.enabled,
    ).ask()

    if not cfg.agents.enabled:
        return

    console.print(
        "\n  [dim]Orchestrator and worker agents can use separate models.\n"
        "  Leave blank to inherit the main LLM provider/model.[/dim]\n"
    )

    models_by_provider = {
        "claude": ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        "openai": ["o3", "o4-mini", "gpt-4o", "gpt-4o-mini"],
        "gemini": ["gemini-2.5-pro", "gemini-2.0-flash"],
    }
    all_models = [m for models in models_by_provider.values() for m in models]

    for role, label in [("orchestrator", "Orchestrator (planner — use strongest model)"),
                        ("worker", "Worker agents (executors — can be cheaper model)")]:
        agent_cfg = getattr(cfg.agents, role)
        change = questionary.confirm(
            f"  Configure {label}? (currently: {agent_cfg.model or 'inherit main LLM'})",
            default=False,
        ).ask()
        if not change:
            continue
        model = questionary.autocomplete(
            f"  Model for {role} (blank = inherit):",
            choices=all_models,
            default=agent_cfg.model or "",
        ).ask() or ""
        agent_cfg.model = model.strip()
        if agent_cfg.model:
            for provider, models in models_by_provider.items():
                if agent_cfg.model in models:
                    agent_cfg.provider = provider
                    break

    max_iter = questionary.text(
        f"  Max agent iterations per task (currently {cfg.agents.max_agent_iterations}):",
        default=str(cfg.agents.max_agent_iterations),
    ).ask() or str(cfg.agents.max_agent_iterations)
    try:
        cfg.agents.max_agent_iterations = int(max_iter)
    except ValueError:
        pass

    console.print(f"  [green]✓ Agents {'enabled' if cfg.agents.enabled else 'disabled'}.[/green]")


def _step_control_panel(cfg: Config, is_update: bool, out_tokens: dict) -> None:
    """Configure control panels.  Multiple panels can run simultaneously."""
    from umabot.config.schema import ControlPanelConfig as _CPConfig

    # Collect all currently active panels for display
    all_active = _all_active_panels(cfg)

    if is_update and all_active:
        active_labels = ", ".join(
            f"{p.ui_type}{'@' + str(p.web_port) if p.ui_type == 'web' else ''}"
            for p in all_active
        )
        console.print(f"  Active panels: [cyan]{active_labels}[/cyan]\n")

    # Loop — keep adding panels until user is done
    while True:
        action = questionary.select(
            "Control panel action:",
            choices=[
                Choice("Add web UI (local browser)",        value="web"),
                Choice("Add Telegram bot panel",            value="telegram"),
                Choice("Add Discord bot panel",             value="discord"),
                Choice("Remove a panel",                    value="remove"),
                Choice("Done",                              value="done"),
            ],
        ).ask()

        if action is None or action == "done":
            break

        if action == "remove":
            if not all_active:
                console.print("[yellow]No panels configured.[/yellow]")
                continue
            labels = [
                f"{p.ui_type}{'@' + str(p.web_port) if p.ui_type == 'web' else ''}"
                for p in all_active
            ]
            idx = questionary.select(
                "Which panel to remove?",
                choices=[Choice(l, value=i) for i, l in enumerate(labels)],
            ).ask()
            if idx is not None:
                removed = all_active.pop(idx)
                console.print(f"[yellow]  → Removed {removed.ui_type} panel.[/yellow]")
            continue

        if action == "web":
            existing_web = next((p for p in all_active if p.ui_type == "web"), None)
            default_port = str(existing_web.web_port if existing_web else 8080)
            port_str = questionary.text("Port for the web panel:", default=default_port).ask()
            try:
                port = int(port_str or default_port)
            except ValueError:
                port = 8080
            if existing_web:
                existing_web.web_port = port
                console.print(f"[green]  → Web panel updated → http://127.0.0.1:{port}[/green]")
            else:
                panel = _CPConfig(enabled=True, ui_type="web", web_host="127.0.0.1", web_port=port)
                all_active.append(panel)
                console.print(f"[green]  → Web panel added → http://127.0.0.1:{port}[/green]")

        elif action == "telegram":
            console.print("\n[cyan]━━━ Telegram Control Panel ━━━[/cyan]")
            console.print("We'll discover your chat ID by having you send a message to your bot.\n")
            from umabot.cli.control_panel_setup import _get_bot_token_and_discover_chat_id
            result = asyncio.run(_get_bot_token_and_discover_chat_id())
            if result:
                token, chat_id, bot_username = result
                out_tokens["telegram"] = token
                connector_name = f"control_panel_tg_{len([p for p in all_active if p.ui_type == 'telegram']) + 1}"
                panel = _CPConfig(
                    enabled=True, ui_type="telegram",
                    connector=connector_name, chat_id=chat_id,
                )
                all_active.append(panel)
                if not any(c.name == connector_name for c in cfg.connectors):
                    cfg.connectors.append(ConnectorConfig(
                        name=connector_name, type="telegram_bot", token=token,
                    ))
                console.print(f"[green]  → Telegram panel added (@{bot_username})[/green]")
            else:
                console.print("[yellow]  → Telegram setup skipped.[/yellow]")

        elif action == "discord":
            console.print("\n[cyan]━━━ Discord Control Panel ━━━[/cyan]")
            token = _prompt_required_secret("Discord bot token:")
            chat_id = questionary.text("Discord channel ID:").ask() or ""
            out_tokens["discord"] = token
            connector_name = f"control_panel_discord_{len([p for p in all_active if p.ui_type == 'discord']) + 1}"
            panel = _CPConfig(
                enabled=True, ui_type="discord",
                connector=connector_name, chat_id=chat_id,
            )
            all_active.append(panel)
            if not any(c.name == connector_name for c in cfg.connectors):
                cfg.connectors.append(ConnectorConfig(
                    name=connector_name, type="discord", token=token,
                ))
            console.print("[green]  → Discord panel added.[/green]")

    # Write back: first panel → primary (legacy compat), rest → control_panels list
    if all_active:
        cfg.control_panel = all_active[0]
        cfg.control_panels = all_active[1:]
    else:
        cfg.control_panel.enabled = False
        cfg.control_panels = []

    if all_active:
        summary = ", ".join(
            f"{p.ui_type}{'@' + str(p.web_port) if p.ui_type == 'web' else ''}"
            for p in all_active
        )
        console.print(f"[green]✓ Active control panels: {summary}[/green]")


def _setup_integrations_interactive(cfg: Config) -> None:
    """Interactive integrations setup — credentials + live OAuth login."""
    while True:
        choice = questionary.select(
            "Add an integration:",
            choices=[
                questionary.Choice("Google Workspace (Gmail, Calendar, Tasks)", value="google"),
                questionary.Choice("Done", value="done"),
            ],
        ).ask()

        if choice is None or choice == "done":
            break

        if choice == "google":
            _setup_google_workspace(cfg)


def _setup_google_workspace(cfg: Config) -> None:
    """Walk the user through Google Workspace credential setup and OAuth login."""
    from umabot.cli.google import _parse_client_secret_json, run_oauth_login, _OAUTH_CALLBACK_PORT
    from umabot.storage import Database

    # ── Step 1: Credentials ───────────────────────────────────────────────
    console.print("\n[bold cyan]━━━ Google Workspace — Step 1 of 3: Credentials ━━━[/bold cyan]\n")
    console.print(
        "You need a Google Cloud OAuth 2.0 credential.  Quick steps:\n\n"
        "  1. Open [link]https://console.cloud.google.com/apis/credentials[/link]\n"
        "  2. Enable APIs: [bold]Gmail API[/bold], [bold]Google Calendar API[/bold], [bold]Tasks API[/bold]\n"
        "  3. Create Credentials → OAuth 2.0 Client ID → [bold]Web application[/bold]\n"
        f"  4. Add Authorized redirect URI:  [cyan]http://127.0.0.1:{_OAUTH_CALLBACK_PORT}/callback[/cyan]\n"
        "  5. Download [bold]client_secret.json[/bold]  OR  copy the values\n"
    )

    json_path = questionary.text(
        "Path to client_secret.json (or press Enter to type manually):"
    ).ask() or ""

    client_id = ""
    client_secret = ""

    if json_path.strip():
        client_id, client_secret = _parse_client_secret_json(json_path.strip())
        if not client_id:
            console.print(f"[red]✗ Could not parse {json_path}. Skipping Google setup.[/red]\n")
            return
        console.print(f"[green]✓ Credentials parsed from {json_path}[/green]")
    else:
        client_id = questionary.text("Client ID:").ask() or ""
        client_secret = questionary.password("Client Secret (hidden):").ask() or ""

    if not client_id or not client_secret:
        console.print("[yellow]Skipping Google Workspace — credentials not provided.[/yellow]\n")
        return

    cfg.integrations.google.client_id = client_id
    cfg.integrations.google.client_secret = client_secret
    console.print("[green]✓ Credentials saved to config.[/green]")

    # ── Step 2: Install deps if needed ────────────────────────────────────
    console.print("\n[bold cyan]━━━ Google Workspace — Step 2 of 3: Dependencies ━━━[/bold cyan]\n")
    try:
        import googleapiclient  # noqa: F401
        console.print("[green]✓ Google API libraries already installed.[/green]")
    except ImportError:
        console.print("[yellow]Installing Google API libraries...[/yellow]")
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q",
             "google-api-python-client>=2.100.0",
             "google-auth-oauthlib>=1.1.0",
             "google-auth-httplib2>=0.2.0"],
            capture_output=True,
        )
        if result.returncode != 0:
            console.print(
                "[red]✗ Failed to install Google libraries.[/red]\n"
                f"[dim]{result.stderr.decode()[:200]}[/dim]\n"
                "[yellow]You can install manually: pip install 'umabot[google]'[/yellow]"
            )
            return
        console.print("[green]✓ Google API libraries installed.[/green]")

    # ── Step 3: OAuth login ───────────────────────────────────────────────
    console.print("\n[bold cyan]━━━ Google Workspace — Step 3 of 3: Authorise ━━━[/bold cyan]\n")
    do_login = questionary.confirm(
        "Open browser now to authorise Google access?", default=True
    ).ask()

    if not do_login:
        console.print(
            "[yellow]Skipping login — run [bold]umabot google login[/bold] later to authorise.[/yellow]\n"
        )
        return

    # Save config first so the DB path is resolved
    from umabot.config.loader import save_config as _save_cfg
    from pathlib import Path as _Path
    _cfg_path = str(_Path.home() / ".umabot" / "config.yaml")
    _save_cfg(cfg, _cfg_path)

    db = Database(cfg.storage.db_path)
    try:
        ok = run_oauth_login(client_id, client_secret, db)
    finally:
        db.close()

    if ok:
        # ── Show status inline ─────────────────────────────────────────
        console.print("\n[bold cyan]━━━ Google Workspace — Status ━━━[/bold cyan]")
        from umabot.tools.google.auth import get_credentials
        try:
            creds = get_credentials(client_id, client_secret, Database(cfg.storage.db_path))
            if creds and creds.valid:
                console.print("  Token  : [green]✅  valid[/green]")
            elif creds and creds.expired:
                console.print("  Token  : [yellow]⚠  expired (will auto-refresh on next use)[/yellow]")
            else:
                console.print("  Token  : [red]✗  invalid[/red]")
        except Exception:
            console.print("  Token  : [green]stored[/green]")
        console.print()

        # ── Gmail watch (proactive notifications) ─────────────────────
        _setup_gmail_watch_interactive(cfg)


def _setup_gmail_watch_interactive(cfg: Config) -> None:
    """Optionally configure a gmail_imap connector for proactive email notifications."""
    want = questionary.confirm(
        "Enable proactive Gmail notifications via IMAP? (can skip and add later)",
        default=False,
    ).ask()
    if not want:
        console.print(
            "[dim]Skipped. To add later: configure a gmail_imap connector in config.yaml.[/dim]\n"
        )
        return

    console.print(
        "\n[bold cyan]━━━ Gmail IMAP — Proactive Notifications ━━━[/bold cyan]\n"
        "[dim]Make sure IMAP is enabled in Gmail:\n"
        "  Gmail → Settings → See all settings → Forwarding and POP/IMAP → Enable IMAP[/dim]\n"
    )

    mailbox = (
        questionary.text("IMAP mailbox to watch:", default="INBOX").ask() or "INBOX"
    ).strip()

    connector_name = (
        questionary.text("Name for this connector:", default="gmail_imap").ask() or "gmail_imap"
    ).strip()

    new_connector = ConnectorConfig(
        name=connector_name,
        type="gmail_imap",
        mailbox=mailbox,
    )
    if cfg.connectors is None:
        cfg.connectors = []
    cfg.connectors.append(new_connector)

    # Describe where notifications will land automatically
    panel_enabled = getattr(getattr(cfg, "control_panel", None), "enabled", False)
    notify_target = "local control panel (http://127.0.0.1:8080)" if panel_enabled else "configured control channel"

    console.print(
        f"\n[green]✓ Gmail IMAP connector '{connector_name}' added.[/green]\n"
        f"  Mailbox    : {mailbox}\n"
        f"  Notifies   : {notify_target} (auto-routed)\n"
    )


def _get_models_for_provider(provider: str) -> list:
    """Return available models for a provider."""
    models = {
        "openai": [
            Choice("o3 — reasoning model, best instruction-following (recommended)", value="o3"),
            Choice("o4-mini — fast reasoning model, great value", value="o4-mini"),
            Choice("GPT-4o — multimodal, no reasoning", value="gpt-4o"),
            Choice("GPT-4o mini — fast, cheap, no reasoning", value="gpt-4o-mini"),
            Choice("o1 — original reasoning model", value="o1"),
        ],
        "claude": [
            Choice("Claude Sonnet 4.6 (recommended)", value="claude-sonnet-4-6"),
            Choice("Claude Opus 4.6", value="claude-opus-4-6"),
            Choice("Claude Haiku 4.5", value="claude-haiku-4-5-20251001"),
        ],
        "gemini": [
            Choice("Gemini 2.0 Flash", value="gemini-2.0-flash"),
            Choice("Gemini 1.5 Pro", value="gemini-1.5-pro"),
            Choice("Gemini 1.5 Flash", value="gemini-1.5-flash"),
        ],
    }
    return models.get(provider, [Choice("Default model", value="default")])


def _setup_connectors_interactive(cfg: Config) -> None:
    """Interactive connector setup with questionary."""
    console.print(
        "\n[dim]Connectors read ALL messages from third-party platforms.[/dim]\n"
        "[dim]You'll authenticate with QR code during setup (one-time only).[/dim]\n"
    )

    # Ensure database exists for session storage
    db_path = Path(cfg.storage.db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    while True:
        # Build connector menu — show Gmail option only when Google is configured
        google_cfg = getattr(getattr(cfg, "integrations", None), "google", None)
        google_ready = bool(getattr(google_cfg, "client_id", "")) if google_cfg else False
        gmail_choice = Choice(
            "Gmail IMAP (proactive email notifications via IMAP IDLE)",
            value="gmail",
        )
        connector_choices = [
            Choice("Telegram (read all messages via MTProto)", value="telegram"),
            Choice("Discord (read all messages)", value="discord"),
        ]
        if google_ready:
            connector_choices.append(gmail_choice)
        else:
            connector_choices.append(
                Choice(
                    "Gmail IMAP (configure Google integration first — Step 3)",
                    value="gmail_disabled",
                )
            )
        connector_choices.append(Choice("Done adding connectors", value="done"))

        connector_type = questionary.select(
            "Add a connector:",
            choices=connector_choices,
        ).ask()

        if connector_type is None or connector_type == "done":
            break

        if connector_type == "gmail_disabled":
            console.print(
                "[yellow]Google integration is not configured yet.\n"
                "Go back to Step 3 (Integrations → Google Workspace) first,[/yellow]\n"
                "[dim]then re-run 'make init' and choose 'Update existing' to add Gmail IMAP.[/dim]\n"
            )
            continue

        if connector_type == "gmail":
            _setup_gmail_watch_interactive(cfg)
            continue

        connector_name = questionary.text(
            "Connector name (e.g., telegram_main):", default=f"{connector_type}_main"
        ).ask()

        if connector_type == "telegram":
            console.print("\n[cyan]━━━ Telegram Setup ━━━[/cyan]\n")
            console.print(
                "[yellow]Why API credentials?[/yellow]\n"
                "Telegram requires API ID/Hash for all third-party clients.\n"
                "This is a [bold]one-time setup[/bold] required by Telegram (not us).\n"
            )
            console.print(
                "\n[cyan]Step 1:[/cyan] Get your API credentials:\n"
                "  1. Visit: [link]https://my.telegram.org/apps[/link]\n"
                "  2. Log in with your phone number\n"
                "  3. Create a new application (any name/platform)\n"
                "  4. Copy the API ID and API Hash\n"
            )

            api_id = questionary.text("\nEnter API ID:").ask()
            api_hash = questionary.password("Enter API Hash:").ask()

            if not api_id or not api_hash:
                console.print("[red]✗ API credentials required. Skipping connector.[/red]\n")
                continue

            console.print("\n[cyan]Step 2:[/cyan] Authenticate with QR code\n")

            # Authenticate with QR code immediately
            session_string = _authenticate_telegram_qr(api_id, api_hash, connector_name, cfg.storage.db_path)

            if session_string:
                cfg.connectors.append(
                    ConnectorConfig(
                        name=connector_name,
                        type="telegram_user",
                        api_id=api_id,
                        api_hash=api_hash,
                        allow_login=True,
                    )
                )
                console.print(f"\n[green]✓ Telegram connector authenticated: {connector_name}[/green]")
                console.print("[dim]  → Session saved! No need to scan QR code again.[/dim]\n")
            else:
                console.print("\n[red]✗ Authentication cancelled or failed. Skipping connector.[/red]\n")

        elif connector_type == "discord":
            console.print("\n[cyan]Discord Setup[/cyan]")
            console.print("Create a bot at: [link]https://discord.com/developers/applications[/link]\n")

            token = _prompt_required_secret("Discord bot token:")
            cfg.connectors.append(
                ConnectorConfig(name=connector_name, type="discord", token=token)
            )
            console.print(f"[green]✓ Added connector: {connector_name}[/green]\n")


def _prompt_required_secret(prompt: str) -> str:
    """Prompt for a secret until a non-empty value is provided."""
    while True:
        value = questionary.password(prompt).ask()
        if value is None:
            raise KeyboardInterrupt
        text = str(value).strip()
        if text:
            return text
        console.print("[yellow]Input cannot be empty.[/yellow]")


def _authenticate_telegram_qr(api_id: str, api_hash: str, connector_name: str, db_path: str) -> Optional[str]:
    """
    Authenticate Telegram connector with QR code.

    Returns session string if successful, None otherwise.
    """
    try:
        # Run async authentication
        return asyncio.run(_do_telegram_qr_auth(api_id, api_hash, connector_name, db_path))
    except KeyboardInterrupt:
        console.print("\n[yellow]Authentication cancelled by user.[/yellow]")
        return None
    except Exception as e:
        console.print(f"\n[red]Authentication error: {e}[/red]")
        return None


async def _do_telegram_qr_auth(api_id: str, api_hash: str, connector_name: str, db_path: str) -> Optional[str]:
    """Async QR code authentication."""
    session = StringSession()
    client = TelegramClient(session, int(api_id), api_hash)

    try:
        await client.connect()

        if not await client.is_user_authorized():
            # Show QR code
            console.print("[cyan]Scan this QR code with Telegram:[/cyan]\n")
            qr_login = await client.qr_login()

            # Print QR code to terminal
            qr = qrcode.QRCode()
            qr.add_data(qr_login.url)
            qr.make(fit=True)
            qr.print_ascii(invert=True)

            console.print(
                "\n[yellow]📱 On your phone:[/yellow]\n"
                "  1. Open Telegram\n"
                "  2. Go to: Settings → Devices → Link Desktop Device\n"
                "  3. Scan the QR code above\n"
            )
            console.print("[dim]Waiting for QR code scan...[/dim]\n")

            # Wait for user to scan (with timeout)
            try:
                await asyncio.wait_for(qr_login.wait(), timeout=120)  # 2 min timeout
            except asyncio.TimeoutError:
                console.print("[red]QR code expired. Please try again.[/red]")
                return None

            console.print("[green]✓ QR code scanned successfully![/green]")

        # Get session string
        session_string = client.session.save()

        # Save session to database
        db = Database(db_path)
        db.upsert_connector_session(connector_name, "telegram_user", session_string.encode("utf-8"))
        db.close()

        await client.disconnect()
        return session_string

    except Exception as e:
        console.print(f"[red]Error during authentication: {e}[/red]")
        await client.disconnect()
        return None


def _ensure_agent_context_file(config_dir: Path) -> None:
    """Copy the AGENT.md template into the user's config dir if it doesn't exist yet."""
    dest = config_dir / "AGENT.md"
    if dest.exists():
        return
    template = Path(__file__).parent.parent.parent / "AGENT.md.template"
    if template.exists():
        dest.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        # Fallback: write a minimal stub inline
        dest.write_text(
            "# Agent Context\n\n"
            "Edit this file to give your assistant standing instructions, persona,\n"
            "personal facts, or domain knowledge. It is injected into every request.\n\n"
            "## About me\n\n"
            "## Preferences\n\n"
            "## Standing rules\n\n"
            "## Domain context\n",
            encoding="utf-8",
        )
    console.print(
        f"[dim]  → Created agent context file: {dest}\n"
        "     Edit it to customise your assistant's behaviour.[/dim]"
    )


def _install_system_daemon(config_file: Path) -> None:
    """Install systemd/launchd service (placeholder)."""
    console.print(
        "\n[yellow]Daemon installation not implemented yet.[/yellow]\n"
        "To run UMA BOT on system startup, configure systemd (Linux) or launchd (macOS) manually.\n"
        "See README for examples."
    )
