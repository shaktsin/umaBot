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
    ControlPanelConfig,
    LLMConfig,
    default_config,
)
from umabot.config.loader import save_config as save_cfg, store_secrets
from umabot.storage import Database

console = Console()


def run_wizard(
    config_path: Optional[str] = None,
    install_daemon: bool = False,
    reset: bool = False,
) -> str:
    """
    Run interactive onboarding wizard with beautiful prompts.

    Args:
        config_path: Path to config file (default: ~/.umabot/config.yaml)
        install_daemon: Install system daemon (systemd/launchd)
        reset: Reset existing configuration

    Returns:
        Path to created config file
    """
    console.print(
        Panel.fit(
            "[bold cyan]UMA BOT Onboarding Wizard[/bold cyan]\n"
            "Let's set up your personal AI assistant",
            border_style="cyan",
        )
    )

    # Determine config file path
    if config_path:
        config_file = Path(config_path).expanduser()
    else:
        config_file = Path.home() / ".umabot" / "config.yaml"

    # Check if config exists
    if config_file.exists() and not reset:
        overwrite = questionary.confirm(
            f"Configuration already exists at {config_file}. Overwrite?",
            default=False,
        ).ask()
        if not overwrite:
            console.print("[yellow]Setup cancelled.[/yellow]")
            return str(config_file)

    # Setup mode
    setup_mode = questionary.select(
        "Choose setup mode:",
        choices=[
            Choice("QuickStart (recommended defaults)", value="quickstart"),
            Choice("Advanced (full configuration)", value="advanced"),
        ],
    ).ask()

    cfg = default_config()

    # Step 1: AI Provider
    console.print("\n[bold]Step 1: AI Provider[/bold]")
    provider = questionary.select(
        "Select your AI provider:",
        choices=[
            Choice("OpenAI (GPT-4, GPT-4o)", value="openai"),
            Choice("Anthropic Claude", value="claude"),
            Choice("Google Gemini", value="gemini"),
        ],
        default="openai",
    ).ask()

    cfg.llm.provider = provider

    # Provider-specific model selection
    models = _get_models_for_provider(provider)
    model = questionary.select(f"Select {provider} model:", choices=models).ask()
    cfg.llm.model = model

    api_key = _prompt_required_secret(f"Enter {provider} API key:")
    cfg.llm.api_key = api_key

    # Step 2: Control Panel Setup
    console.print("\n[bold]Step 2: Control Panel[/bold]")
    console.print(
        "The control panel is YOUR private interface for confirmations and management.\n"
        "This is separate from message connectors (which handle external messages)."
    )

    setup_control = questionary.confirm("Set up control panel now?", default=True).ask()

    if setup_control:
        ui_type = questionary.select(
            "Choose control panel UI type:",
            choices=[
                Choice("Telegram Bot (remote messaging)", value="telegram"),
                Choice("Discord Bot (remote messaging)", value="discord"),
                Choice("CLI Chat (local terminal) [Coming Soon]", value="cli"),
                Choice("Web UI (local browser) [Coming Soon]", value="web"),
                Choice("Skip for now", value="none"),
            ],
        ).ask()

        if ui_type == "telegram":
            console.print("\n[cyan]━━━ Telegram Control Panel Setup ━━━[/cyan]\n")
            console.print(
                "We'll automatically discover your chat ID by having you send a message to your bot.\n"
            )

            # Use automatic setup from control_panel_setup
            from umabot.cli.control_panel_setup import _get_bot_token_and_discover_chat_id

            result = asyncio.run(_get_bot_token_and_discover_chat_id())

            if result:
                telegram_token, chat_id, bot_username = result

                cfg.control_panel.enabled = True
                cfg.control_panel.ui_type = "telegram"
                cfg.control_panel.connector = "control_panel_bot"
                cfg.control_panel.chat_id = chat_id

                # Add control panel connector
                cfg.connectors.append(
                    ConnectorConfig(
                        name="control_panel_bot",
                        type="telegram_bot",
                        token=telegram_token
                    )
                )
                console.print(f"[green]✓ Control panel configured via Telegram (@{bot_username})[/green]")
            else:
                console.print("[yellow]Control panel setup skipped[/yellow]")

        elif ui_type == "discord":
            discord_token = _prompt_required_secret(
                "Enter Discord bot token for control panel:"
            )

            chat_id = questionary.text("Enter your Discord channel ID:").ask()

            cfg.control_panel.enabled = True
            cfg.control_panel.ui_type = "discord"
            cfg.control_panel.connector = "control_panel_bot"
            cfg.control_panel.chat_id = chat_id

            cfg.connectors.append(
                ConnectorConfig(
                    name="control_panel_bot",
                    type="discord",
                    token=discord_token
                )
            )
            console.print("[green]✓ Control panel configured via Discord[/green]")

        elif ui_type in ("cli", "web"):
            console.print(f"[yellow]✓ {ui_type.upper()} control panel will be available in future release[/yellow]")
            cfg.control_panel.enabled = False

    # Step 3: Message Connectors
    console.print("\n[bold]Step 3: Message Connectors[/bold]")
    console.print("Message connectors receive messages from various platforms.")

    add_connectors = questionary.confirm("Add message connectors?", default=True).ask()

    if add_connectors:
        _setup_connectors_interactive(cfg)

    # Step 4: Tools
    console.print("\n[bold]Step 4: Tools[/bold]")

    cfg.tools.shell_enabled = questionary.confirm(
        "Enable shell command tool? (requires careful security)", default=False
    ).ask()

    # Step 5: Security (if advanced mode)
    if setup_mode == "advanced":
        strictness = questionary.select(
            "Confirmation strictness:",
            choices=[
                Choice("Normal (confirm RED tier only)", value="normal"),
                Choice("Strict (confirm all tools)", value="strict"),
            ],
            default="normal",
        ).ask()
        cfg.policy.confirmation_strictness = strictness

    # Step 6: Generate WebSocket token
    cfg.runtime.ws_token = secrets.token_urlsafe(32)

    # Create config directory if needed
    config_file.parent.mkdir(parents=True, exist_ok=True)

    # Save configuration
    save_cfg(cfg, str(config_file))
    store_secrets(api_key=api_key)

    console.print(
        Panel.fit(
            f"[bold green]✓ Configuration saved to {config_file}[/bold green]\n\n"
            "Next steps:\n"
            "  • Run [cyan]umabot doctor[/cyan] to verify setup\n"
            "  • Run [cyan]umabot start[/cyan] to start daemon\n"
            "  • Run [cyan]umabot connections[/cyan] to check status",
            border_style="green",
        )
    )

    # Optional daemon installation
    if install_daemon:
        _install_system_daemon(config_file)

    return str(config_file)


def _get_models_for_provider(provider: str) -> list:
    """Return available models for a provider."""
    models = {
        "openai": [
            Choice("GPT-4o (recommended)", value="gpt-4o"),
            Choice("GPT-4o mini", value="gpt-4o-mini"),
            Choice("GPT-4 Turbo", value="gpt-4-turbo"),
        ],
        "claude": [
            Choice("Claude 3.5 Sonnet", value="claude-3-5-sonnet-20240620"),
            Choice("Claude 3 Opus", value="claude-3-opus-20240229"),
            Choice("Claude 3 Haiku", value="claude-3-haiku-20240307"),
        ],
        "gemini": [
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
        connector_type = questionary.select(
            "Add a connector:",
            choices=[
                Choice("Telegram (read all messages)", value="telegram"),
                Choice("Discord (read all messages)", value="discord"),
                Choice("Done adding connectors", value="done"),
            ],
        ).ask()

        if connector_type == "done":
            break

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
            raise SystemExit("Onboarding cancelled.")
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


def _install_system_daemon(config_file: Path) -> None:
    """Install systemd/launchd service (placeholder)."""
    console.print(
        "\n[yellow]Daemon installation not implemented yet.[/yellow]\n"
        "To run UMA BOT on system startup, configure systemd (Linux) or launchd (macOS) manually.\n"
        "See README for examples."
    )
