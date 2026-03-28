"""Control panel setup wizard for automatic chat ID discovery."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from umabot.config import load_config, save_config

console = Console()
logger = logging.getLogger("umabot.cli.control_panel_setup")


async def _get_bot_token_and_discover_chat_id() -> Optional[tuple[str, str, str]]:
    """
    Interactive prompt to get bot token and discover chat ID.

    Returns:
        Tuple of (token, chat_id, bot_username) if successful, None otherwise
    """
    # Ask for bot token
    console.print("\n[bold]Telegram Bot Token[/bold]")
    console.print("You need a Telegram bot token from @BotFather")
    console.print("👉 Open Telegram and message @BotFather")
    console.print("👉 Send: /newbot")
    console.print("👉 Follow instructions and copy the token\n")

    token = console.input("[cyan]Enter your bot token:[/cyan] ").strip()

    if not token:
        console.print("[red]✗[/red] Token is required")
        return None

    # Verify bot token works
    console.print("\n[bold]Verifying bot token...[/bold]")
    try:
        bot_info = await asyncio.to_thread(_get_me, token)
        bot_username = bot_info.get("result", {}).get("username", "unknown")
        console.print(f"[green]✓[/green] Bot verified: @{bot_username}")
    except Exception as e:
        console.print(f"[red]✗[/red] Invalid bot token: {e}")
        return None

    # Wait for owner to send message
    console.print(f"\n[bold]Send a message to your bot[/bold]")
    console.print(f"👉 Open Telegram and search for: @{bot_username}")
    console.print(f"👉 Send any message (like /start) to the bot\n")

    chat_id = None
    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]Waiting for your message...[/cyan]"),
        console=console
    ) as progress:
        task = progress.add_task("waiting", total=None)

        offset = None
        max_attempts = 60  # Wait up to 60 seconds

        for _ in range(max_attempts):
            try:
                updates = await asyncio.to_thread(_get_updates, token, offset, timeout=1)

                for update in updates.get("result", []) or []:
                    offset = update.get("update_id", 0) + 1
                    message = update.get("message")

                    if message and "chat" in message:
                        chat_id = str(message["chat"]["id"])
                        user_name = message.get("from", {}).get("first_name", "User")
                        console.print(f"\n[green]✓[/green] Received message from: {user_name}")
                        console.print(f"[green]✓[/green] Chat ID: {chat_id}")
                        break

                if chat_id:
                    break

            except Exception as e:
                logger.debug(f"Polling error: {e}")

            await asyncio.sleep(1)
        else:
            console.print("\n[red]✗[/red] Timeout waiting for message")
            console.print("Please make sure you sent a message to the bot and try again")
            return None

    if not chat_id:
        console.print("[red]✗[/red] Failed to get chat ID")
        return None

    # Send confirmation message
    try:
        await asyncio.to_thread(
            _send_message,
            token,
            chat_id,
            "✅ Control panel setup complete!\n\n"
            "This is your private control panel. You'll receive:\n"
            "• Confirmations for sensitive actions (🔴 RED tools)\n"
            "• Task execution results\n"
            "• System notifications"
        )
        console.print("[green]✓[/green] Sent confirmation to your Telegram")
    except Exception as e:
        logger.warning(f"Failed to send confirmation: {e}")

    return (token, chat_id, bot_username)


async def setup_telegram_control_panel(config_path: Optional[str] = None) -> bool:
    """
    Interactive setup for Telegram control panel.

    Automatically discovers owner's chat ID by waiting for them to send a message.

    Returns:
        True if setup succeeded, False otherwise
    """
    console.print(Panel.fit(
        "[bold cyan]Telegram Control Panel Setup[/bold cyan]\n\n"
        "This wizard will help you set up your private control panel.",
        border_style="cyan"
    ))

    # Load config
    try:
        cfg, resolved_path = load_config(config_path=config_path)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to load config: {e}")
        return False

    # Get token and discover chat ID
    result = await _get_bot_token_and_discover_chat_id()
    if not result:
        return False

    token, chat_id, bot_username = result

    # Update config
    console.print("\n[bold]Step 3: Saving configuration...[/bold]")

    # Find or create control panel connector
    connector_name = "control_panel_bot"
    connector_exists = False

    for i, conn in enumerate(cfg.connectors):
        # Support both dict and object formats
        conn_name = conn.get("name") if isinstance(conn, dict) else conn.name
        if conn_name == connector_name:
            if isinstance(conn, dict):
                cfg.connectors[i]["token"] = token
            else:
                cfg.connectors[i].token = token
            connector_exists = True
            break

    if not connector_exists:
        from umabot.config.schema import ConnectorConfig
        cfg.connectors.append(ConnectorConfig(
            name=connector_name,
            type="telegram_bot",
            token=token
        ))

    # Update control panel config
    cfg.control_panel.enabled = True
    cfg.control_panel.ui_type = "telegram"
    cfg.control_panel.connector = connector_name
    cfg.control_panel.chat_id = chat_id

    # Save config
    try:
        save_config(cfg, resolved_path)
        console.print(f"[green]✓[/green] Configuration saved to: {resolved_path}")
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to save config: {e}")
        return False

    # Success summary
    console.print(Panel.fit(
        f"[bold green]✓ Setup Complete![/bold green]\n\n"
        f"Your control panel is ready:\n"
        f"  • Bot: @{bot_username}\n"
        f"  • Chat ID: {chat_id}\n"
        f"  • Connector: {connector_name}\n\n"
        "[cyan]Next steps:[/cyan]\n"
        f"  1. Start UmaBot: [bold]make start[/bold]\n"
        f"  2. The bot will use this chat for confirmations\n"
        f"  3. Test it by triggering a 🔴 RED tool (like shell.run)",
        border_style="green"
    ))

    return True


def _get_me(token: str) -> dict:
    """Get bot information."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    req = Request(url, headers={"User-Agent": "umabot/0.1"})
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_updates(token: str, offset: Optional[int], timeout: int = 20) -> dict:
    """Poll for Telegram updates."""
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset

    url = f"https://api.telegram.org/bot{token}/getUpdates?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "umabot/0.1"})
    with urlopen(req, timeout=timeout + 5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _send_message(token: str, chat_id: str, text: str) -> dict:
    """Send message to Telegram chat."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_setup(config_path: Optional[str] = None) -> None:
    """CLI entry point for control panel setup."""
    try:
        success = asyncio.run(setup_telegram_control_panel(config_path))
        if not success:
            raise SystemExit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Setup cancelled by user[/yellow]")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"\n[red]Setup failed: {e}[/red]")
        logger.exception("Setup error")
        raise SystemExit(1)
