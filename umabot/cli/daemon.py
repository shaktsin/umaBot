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


def _panel_pid_file(cfg) -> Path:
    return Path(cfg.runtime.pid_file).parent / "panel.pid"


def _is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


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
    console.print(f"  Logs: {log_file}")

    # Auto-start web control panel if configured
    if getattr(cfg.control_panel, "enabled", False) and getattr(cfg.control_panel, "ui_type", "") == "web":
        _start_panel(cfg, resolved_path, log_dir, log_level)


def _start_panel(cfg, config_path: str, log_dir: Path, log_level: Optional[str]) -> None:
    """Start the web control panel as a background process."""
    panel_pid_file = _panel_pid_file(cfg)

    # Check if already running
    if panel_pid_file.exists():
        try:
            pid = int(panel_pid_file.read_text().strip())
            if _is_process_running(pid):
                host = getattr(cfg.control_panel, "web_host", "127.0.0.1")
                port = getattr(cfg.control_panel, "web_port", 8080)
                console.print(f"[yellow]Panel already running (PID: {pid}) → http://{host}:{port}[/yellow]")
                return
        except (OSError, ValueError):
            pass
        panel_pid_file.unlink(missing_ok=True)

    host = getattr(cfg.control_panel, "web_host", "127.0.0.1")
    port = getattr(cfg.control_panel, "web_port", 8080)

    cmd = [
        sys.executable, "-m", "umabot.controlpanel",
        "--config", config_path,
        "--host", host,
        "--port", str(port),
        "--no-open",
    ]
    if log_level:
        cmd.extend(["--log-level", log_level])

    panel_log = log_dir / "panel.log"
    with open(panel_log, "a") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True)

    panel_pid_file.write_text(str(proc.pid))
    console.print(f"[green]✓ Control panel started (PID: {proc.pid}) → http://{host}:{port}[/green]")
    console.print(f"  Logs: {panel_log}")


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
        pid_file.unlink(missing_ok=True)

    # Also stop panel if running
    panel_pid_file = _panel_pid_file(cfg)
    if panel_pid_file.exists():
        try:
            pid = int(panel_pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            console.print(f"[green]✓ Sent SIGTERM to control panel (PID: {pid})[/green]")
        except (OSError, ValueError):
            pass
        panel_pid_file.unlink(missing_ok=True)


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
    else:
        try:
            pid = int(pid_file.read_text().strip())
            if _is_process_running(pid):
                console.print(f"[green]● Daemon running (PID: {pid})[/green]")
            else:
                console.print("[yellow]● Daemon not running (stale PID file)[/yellow]")
                pid_file.unlink(missing_ok=True)
        except (OSError, ValueError):
            console.print("[yellow]● Daemon not running (stale PID file)[/yellow]")
            pid_file.unlink(missing_ok=True)

    # Panel status
    panel_pid_file = _panel_pid_file(cfg)
    if panel_pid_file.exists():
        try:
            pid = int(panel_pid_file.read_text().strip())
            if _is_process_running(pid):
                host = getattr(cfg.control_panel, "web_host", "127.0.0.1")
                port = getattr(cfg.control_panel, "web_port", 8080)
                console.print(
                    f"[green]● Control panel running (PID: {pid}) → "
                    f"[link]http://{host}:{port}[/link][/green]"
                )
            else:
                console.print("[yellow]● Control panel not running (stale PID file)[/yellow]")
                panel_pid_file.unlink(missing_ok=True)
        except (OSError, ValueError):
            console.print("[yellow]● Control panel not running[/yellow]")
            panel_pid_file.unlink(missing_ok=True)
    elif getattr(cfg.control_panel, "enabled", False) and getattr(cfg.control_panel, "ui_type", "") == "web":
        host = getattr(cfg.control_panel, "web_host", "127.0.0.1")
        port = getattr(cfg.control_panel, "web_port", 8080)
        console.print(
            f"[yellow]● Control panel configured but not running → "
            f"[cyan]umabot panel[/cyan] or [cyan]umabot start[/cyan][/yellow]"
        )
