"""Connection status viewer command."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich import box

from umabot.config import load_config
from umabot.storage import Database

console = Console()


def show_status(config_path: Optional[str] = None, live: bool = False) -> None:
    """
    Show real-time status of all connectors.

    Similar to `docker ps` or `kubectl get pods`.

    Args:
        config_path: Path to config file
        live: Enable live updating view
    """
    cfg, _ = load_config(config_path=config_path)

    # Check if database exists
    db_path = Path(cfg.storage.db_path)
    if not db_path.exists():
        console.print("[yellow]Database not found. Start the daemon first with:[/yellow]")
        console.print("  [cyan]umabot start[/cyan]")
        return

    db = Database(cfg.storage.db_path)

    if live:
        console.print("[dim]Live view enabled. Press Ctrl+C to exit.[/dim]\n")
        try:
            with Live(_generate_status_table(db, cfg), refresh_per_second=1, console=console) as live_display:
                while True:
                    time.sleep(1)
                    live_display.update(_generate_status_table(db, cfg))
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped live view.[/dim]")
    else:
        console.print(_generate_status_table(db, cfg))

    db.close()


def _generate_status_table(db: Database, cfg) -> Table:
    """Generate connection status table."""
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        title="[bold]Active Connections[/bold]",
        title_style="cyan",
    )
    table.add_column("Connector", style="cyan")
    table.add_column("Type")
    table.add_column("Channel")
    table.add_column("Status", justify="center")
    table.add_column("Last Active")

    # Query connector_status table
    try:
        with db._lock:
            rows = db._conn.execute(
                """
                SELECT connector, channel, mode, status, updated_at
                FROM connector_status
                WHERE id IN (
                    SELECT MAX(id) FROM connector_status GROUP BY connector
                )
                ORDER BY connector
                """
            ).fetchall()
    except Exception:
        # Table might not exist yet
        rows = []

    # Map configured connectors to their status
    connector_map = {}
    for conn in cfg.connectors:
        if isinstance(conn, dict):
            connector_map[conn.get("name", "")] = conn
        else:
            connector_map[conn.name] = conn

    # Display active connectors
    for row in rows:
        connector_name = row["connector"]
        status = row["status"]

        # Get connector config
        conn_cfg = connector_map.get(connector_name)
        if conn_cfg:
            conn_type = conn_cfg.get("type") if isinstance(conn_cfg, dict) else conn_cfg.type
        else:
            conn_type = "unknown"

        # Status styling
        status_display = {
            "connected": "[green]●[/green] Connected",
            "connecting": "[yellow]◐[/yellow] Connecting",
            "disconnected": "[red]○[/red] Disconnected",
            "error": "[red]✗[/red] Error",
        }.get(status, status)

        # Time formatting
        updated = row["updated_at"]
        time_ago = _time_ago(updated)

        table.add_row(
            connector_name,
            conn_type,
            row["channel"],
            status_display,
            time_ago,
        )

    # Add configured connectors that haven't reported status
    reported_connectors = {row["connector"] for row in rows}
    for conn in cfg.connectors:
        conn_name = conn.get("name") if isinstance(conn, dict) else conn.name
        if conn_name not in reported_connectors:
            conn_type = conn.get("type") if isinstance(conn, dict) else conn.type
            channel = _channel_from_type(conn_type)

            table.add_row(
                conn_name,
                conn_type,
                channel,
                "[dim]○[/dim] Never started",
                "—",
            )

    return table


def _time_ago(timestamp: str) -> str:
    """Convert ISO timestamp to 'X minutes ago' format."""
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = datetime.utcnow()
        delta = now - dt.replace(tzinfo=None)

        if delta.total_seconds() < 60:
            return f"{int(delta.total_seconds())}s ago"
        elif delta.total_seconds() < 3600:
            return f"{int(delta.total_seconds() // 60)}m ago"
        elif delta.total_seconds() < 86400:
            return f"{int(delta.total_seconds() // 3600)}h ago"
        else:
            return f"{delta.days}d ago"
    except Exception:
        return timestamp


def _channel_from_type(conn_type: str) -> str:
    """Map connector type to channel name."""
    if "telegram" in conn_type:
        return "telegram"
    elif "discord" in conn_type:
        return "discord"
    elif "whatsapp" in conn_type:
        return "whatsapp"
    return conn_type
