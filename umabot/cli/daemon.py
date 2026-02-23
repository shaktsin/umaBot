"""Daemon management commands."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console

from umabot.config import load_config

console = Console()


def start_daemon(config_path: Optional[str], log_level: Optional[str]) -> None:
    """Start UMA BOT daemon in background."""
    cfg, resolved_path = load_config(config_path=config_path)
    pid_file = Path(cfg.runtime.pid_file)

    # Check if already running
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # Check if process exists
            console.print(f"[yellow]Daemon already running (PID: {pid})[/yellow]")
            return
        except (OSError, ValueError):
            # Stale PID file
            pid_file.unlink()

    # Start orchestrator in background
    cmd = [sys.executable, "-m", "umabot.orchestrator", "--config", resolved_path]
    if log_level:
        cmd.extend(["--log-level", log_level])

    # Ensure log directory exists
    log_dir = Path(cfg.runtime.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "umabot.log"

    with open(log_file, "a") as log:
        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )

    # Write PID file
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(proc.pid))

    console.print(f"[green]✓ UMA BOT daemon started (PID: {proc.pid})[/green]")
    console.print(f"Logs: {log_file}")


def stop_daemon(config_path: Optional[str]) -> None:
    """Stop UMA BOT daemon."""
    cfg, _ = load_config(config_path=config_path)
    pid_file = Path(cfg.runtime.pid_file)

    if not pid_file.exists():
        console.print("[yellow]Daemon not running (no PID file)[/yellow]")
        return

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        console.print(f"[green]✓ Sent SIGTERM to daemon (PID: {pid})[/green]")
        pid_file.unlink()
    except (OSError, ValueError) as e:
        console.print(f"[red]Error stopping daemon: {e}[/red]")
        pid_file.unlink()


def reload_daemon(config_path: Optional[str]) -> None:
    """Reload daemon configuration (SIGHUP)."""
    cfg, _ = load_config(config_path=config_path)
    pid_file = Path(cfg.runtime.pid_file)

    if not pid_file.exists():
        console.print("[yellow]Daemon not running (no PID file)[/yellow]")
        return

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGHUP)
        console.print(f"[green]✓ Sent SIGHUP to daemon (PID: {pid})[/green]")
    except (OSError, ValueError) as e:
        console.print(f"[red]Error reloading daemon: {e}[/red]")


def show_daemon_status(config_path: Optional[str]) -> None:
    """Show daemon status."""
    cfg, _ = load_config(config_path=config_path)
    pid_file = Path(cfg.runtime.pid_file)

    if not pid_file.exists():
        console.print("[yellow]Daemon not running[/yellow]")
        return

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # Check if process exists
        console.print(f"[green]Daemon running (PID: {pid})[/green]")
    except (OSError, ValueError):
        console.print("[yellow]Daemon not running (stale PID file)[/yellow]")
        pid_file.unlink()
