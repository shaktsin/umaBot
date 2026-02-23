"""System diagnostics command."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich import box

from umabot.config import load_config
from umabot.storage import Database

console = Console()


def run_diagnostics(config_path: Optional[str] = None) -> None:
    """
    Run comprehensive system diagnostics.

    Checks:
    - Configuration validity
    - Database connectivity
    - Connector credentials
    - File system permissions
    - Daemon status
    """
    console.print("\n[bold cyan]UMA BOT System Diagnostics[/bold cyan]\n")

    results = []

    # Check 1: Configuration
    console.print("[dim]Checking configuration...[/dim]")
    try:
        cfg, resolved_path = load_config(config_path=config_path)
        results.append(("Configuration", "✓", f"Loaded from {resolved_path}"))
    except Exception as e:
        results.append(("Configuration", "✗", f"Error: {e}"))
        _print_results(results)
        return

    # Check 2: Database
    console.print("[dim]Checking database...[/dim]")
    try:
        db = Database(cfg.storage.db_path)
        # Try a simple query
        db.close()
        results.append(("Database", "✓", f"Accessible at {cfg.storage.db_path}"))
    except Exception as e:
        results.append(("Database", "✗", f"Error: {e}"))

    # Check 3: Vault directory
    console.print("[dim]Checking vault directory...[/dim]")
    vault = Path(cfg.storage.vault_dir)
    if vault.exists() and vault.is_dir():
        if os.access(vault, os.W_OK):
            results.append(("Vault", "✓", f"Writable at {cfg.storage.vault_dir}"))
        else:
            results.append(("Vault", "⚠", f"Not writable: {cfg.storage.vault_dir}"))
    else:
        results.append(("Vault", "⚠", f"Does not exist: {cfg.storage.vault_dir}"))

    # Check 4: LLM API key
    console.print("[dim]Checking LLM configuration...[/dim]")
    if cfg.llm.api_key:
        results.append(("LLM API Key", "✓", f"{cfg.llm.provider} key configured"))
    else:
        results.append(("LLM API Key", "✗", "No API key configured"))

    # Check 5: Control Panel
    console.print("[dim]Checking control panel...[/dim]")
    if cfg.control_panel.enabled:
        if cfg.control_panel.chat_id:
            results.append((
                "Control Panel",
                "✓",
                f"Enabled via {cfg.control_panel.connector}"
            ))
        else:
            results.append(("Control Panel", "⚠", "Enabled but no chat_id configured"))
    else:
        results.append(("Control Panel", "○", "Not configured"))

    # Check 6: Connectors
    console.print("[dim]Checking connectors...[/dim]")
    if cfg.connectors:
        results.append(("Connectors", "✓", f"{len(cfg.connectors)} configured"))
        for conn in cfg.connectors:
            status = _check_connector_credentials(conn, cfg)
            conn_name = conn.name if hasattr(conn, "name") else conn.get("name", "unknown")
            results.append((f"  └─ {conn_name}", status[0], status[1]))
    else:
        results.append(("Connectors", "⚠", "No connectors configured"))

    # Check 7: WebSocket token
    if cfg.runtime.ws_token:
        results.append(("WebSocket", "✓", "Token configured"))
    else:
        results.append(("WebSocket", "⚠", "No token - connectors cannot connect"))

    # Check 8: PID file (daemon status)
    console.print("[dim]Checking daemon status...[/dim]")
    pid_file = Path(cfg.runtime.pid_file)
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # Check if process exists
            results.append(("Daemon", "✓", f"Running (PID: {pid})"))
        except (OSError, ValueError):
            results.append(("Daemon", "○", "Not running (stale PID file)"))
    else:
        results.append(("Daemon", "○", "Not running"))

    _print_results(results)


def _print_results(results: list) -> None:
    """Print diagnostic results in a nice table."""
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("Component", style="cyan")
    table.add_column("Status", justify="center", width=8)
    table.add_column("Details")

    for component, status, details in results:
        if status == "✓":
            style = "green"
        elif status == "✗":
            style = "red"
        elif status == "⚠":
            style="yellow"
        else:
            style = "dim"

        table.add_row(component, f"[{style}]{status}[/{style}]", details)

    console.print("\n")
    console.print(table)
    console.print("\n")


def _check_connector_credentials(connector, cfg) -> tuple[str, str]:
    """Check if connector has required credentials."""
    # Support both dict and object formats
    if isinstance(connector, dict):
        conn_type = connector.get("type", "")
        token = connector.get("token")
        api_id = connector.get("api_id")
        api_hash = connector.get("api_hash")
    else:
        conn_type = getattr(connector, "type", "")
        token = getattr(connector, "token", None)
        api_id = getattr(connector, "api_id", None)
        api_hash = getattr(connector, "api_hash", None)

    if conn_type == "telegram_bot":
        if token:
            return ("✓", "Token present")
        # Fallback to legacy env/keychain-loaded token
        fallback = os.environ.get("UMABOT_TELEGRAM_TOKEN") or getattr(cfg.telegram, "token", None)
        if fallback:
            return ("✓", "Token present (from UMABOT_TELEGRAM_TOKEN)")
        return ("✗", "Missing token")
    elif conn_type == "telegram_user":
        has_creds = api_id and api_hash
        return ("✓", "Credentials present") if has_creds else ("✗", "Missing API credentials")
    elif conn_type == "discord":
        if token:
            return ("✓", "Token present")
        fallback = os.environ.get("UMABOT_DISCORD_TOKEN") or getattr(cfg.discord, "token", None)
        if fallback:
            return ("✓", "Token present (from UMABOT_DISCORD_TOKEN)")
        return ("✗", "Missing token")
    return ("⚠", "Unknown type")
