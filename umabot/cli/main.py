"""Modern CLI entry point for UMA BOT."""

from __future__ import annotations

import argparse
import sys
from typing import Optional


def main() -> None:
    """CLI entry point with improved command structure."""
    parser = argparse.ArgumentParser(
        prog="umabot",
        description="Self-hosted personal AI assistant",
        epilog="For more help, visit: https://github.com/yourusername/umabot",
    )

    parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Primary onboarding command (ClawBot-like)
    onboard_parser = subparsers.add_parser(
        "onboard", help="Interactive setup wizard (recommended for first-time setup)"
    )
    onboard_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    onboard_parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")
    onboard_parser.add_argument(
        "--install-daemon", action="store_true", help="Install system daemon"
    )
    onboard_parser.add_argument(
        "--reset", action="store_true", help="Reset configuration and start fresh"
    )

    # System diagnostics
    doctor_parser = subparsers.add_parser("doctor", help="Run system diagnostics")
    doctor_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    doctor_parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")

    # Connection status
    connections_parser = subparsers.add_parser(
        "connections", help="Show status of all connectors"
    )
    connections_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    connections_parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")
    connections_parser.add_argument(
        "--live", action="store_true", help="Live updating view"
    )

    # Daemon management commands
    start_parser = subparsers.add_parser("start", help="Start UMA BOT daemon")
    start_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    start_parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")
    stop_parser = subparsers.add_parser("stop", help="Stop UMA BOT daemon")
    stop_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    stop_parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")
    status_parser = subparsers.add_parser("status", help="Show daemon status")
    status_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    status_parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")
    reload_parser = subparsers.add_parser("reload", help="Reload daemon configuration (SIGHUP)")
    reload_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    reload_parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")

    # Orchestrator (dev mode)
    orchestrate_parser = subparsers.add_parser(
        "orchestrate", help="Run gateway + connectors (development mode)"
    )
    orchestrate_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    orchestrate_parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")

    # Channel workers
    channels_parser = subparsers.add_parser(
        "channels", help="Run individual channel workers"
    )
    channels_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    channels_parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")
    channels_subparsers = channels_parser.add_subparsers(dest="channel")

    telegram_parser = channels_subparsers.add_parser("telegram", help="Telegram Bot connector")
    telegram_parser.add_argument("--connector", required=True)
    telegram_parser.add_argument("--token", default=None)

    telegram_user_parser = channels_subparsers.add_parser(
        "telegram-user", help="Telegram User connector"
    )
    telegram_user_parser.add_argument("--connector", required=True)
    telegram_user_parser.add_argument("--login", action="store_true")

    # Skills management
    skills_parser = subparsers.add_parser("skills", help="Manage skills")
    skills_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    skills_subparsers = skills_parser.add_subparsers(dest="skills_command")
    skills_parser.set_defaults(skills_command="list")
    skills_list_parser = skills_subparsers.add_parser("list", help="List installed skills")
    skills_list_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    skills_install_parser = skills_subparsers.add_parser("install", help="Install skill")
    skills_install_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    skills_install_parser.add_argument("path")
    skills_remove_parser = skills_subparsers.add_parser("remove", help="Remove skill")
    skills_remove_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    skills_remove_parser.add_argument("name")
    skills_uninstall_parser = skills_subparsers.add_parser("uninstall", help="Uninstall skill")
    skills_uninstall_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    skills_uninstall_parser.add_argument("name")
    skills_lint_parser = skills_subparsers.add_parser("lint", help="Lint all skills")
    skills_lint_parser.add_argument("--config", dest="config", default=None, help="Path to config file")

    # Task management
    tasks_parser = subparsers.add_parser("tasks", help="Manage scheduled tasks")
    tasks_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    tasks_subparsers = tasks_parser.add_subparsers(dest="tasks_command")
    tasks_list_parser = tasks_subparsers.add_parser("list", help="List tasks")
    tasks_list_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    tasks_list_parser.add_argument("--status", dest="status", default=None, choices=["active", "completed", "cancelled"])
    tasks_create_parser = tasks_subparsers.add_parser("create", help="Create task")
    tasks_create_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    tasks_create_parser.add_argument("--name", required=True)
    tasks_create_parser.add_argument("--prompt", required=True)
    tasks_create_parser.add_argument("--type", required=True, choices=["one_time", "periodic"])
    tasks_create_parser.add_argument("--run-at", dest="run_at", default=None, help="ISO datetime for one_time")
    tasks_create_parser.add_argument("--frequency", dest="frequency", default=None, choices=["hourly", "daily", "weekly"])
    tasks_create_parser.add_argument("--time", dest="time", default=None, help="HH:MM for periodic daily/weekly")
    tasks_create_parser.add_argument("--day-of-week", dest="day_of_week", default=None, help="mon..sun for weekly")
    tasks_create_parser.add_argument("--timezone", dest="timezone", default="UTC")
    tasks_cancel_parser = tasks_subparsers.add_parser("cancel", help="Cancel task")
    tasks_cancel_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    tasks_cancel_parser.add_argument("id", type=int)

    # Web control panel
    panel_parser = subparsers.add_parser("panel", help="Start local web control panel (http://127.0.0.1:8080)")
    panel_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    panel_parser.add_argument("--host", dest="host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    panel_parser.add_argument("--port", dest="port", type=int, default=8080, help="Bind port (default: 8080)")
    panel_parser.add_argument("--no-open", dest="no_open", action="store_true", help="Don't open browser automatically")
    panel_parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")

    # Control panel management
    control_panel_parser = subparsers.add_parser("control-panel", help="Manage control panel")
    control_panel_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    control_panel_parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")
    control_panel_subparsers = control_panel_parser.add_subparsers(dest="control_panel_command")
    control_panel_setup_parser = control_panel_subparsers.add_parser("setup", help="Interactive setup to get Telegram chat ID automatically")
    control_panel_setup_parser.add_argument("--config", dest="config", default=None, help="Path to config file")
    control_panel_setup_parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level")

    args = parser.parse_args()

    # Route to command handlers
    if not args.command:
        parser.print_help()
        return

    # Import handlers on demand
    if args.command == "onboard":
        from umabot.cli.onboard import run_wizard

        run_wizard(
            config_path=args.config,
            install_daemon=args.install_daemon,
            reset=args.reset,
        )

    elif args.command == "doctor":
        from umabot.cli.doctor import run_diagnostics

        run_diagnostics(config_path=args.config)

    elif args.command == "connections":
        from umabot.cli.connections import show_status

        show_status(config_path=args.config, live=args.live)

    elif args.command == "start":
        from umabot.cli.daemon import start_daemon

        start_daemon(args.config, args.log_level)

    elif args.command == "stop":
        from umabot.cli.daemon import stop_daemon

        stop_daemon(args.config)

    elif args.command == "status":
        from umabot.cli.daemon import show_daemon_status

        show_daemon_status(args.config)

    elif args.command == "reload":
        from umabot.cli.daemon import reload_daemon

        reload_daemon(args.config)

    elif args.command == "orchestrate":
        from umabot.orchestrator import main as orchestrate_main

        orchestrate_main(config_path=args.config, log_level=args.log_level)

    elif args.command == "channels":
        if args.channel == "telegram":
            # Legacy telegram bot worker
            from umabot.connectors.telegram_bot_connector import main as telegram_main

            telegram_main()
        elif args.channel == "telegram-user":
            from umabot.connectors.telegram_user_connector import main as telegram_user_main

            telegram_user_main()
        else:
            print("Unknown channel type")
            sys.exit(1)

    elif args.command == "skills":
        from umabot.cli.skills import handle_skills

        handle_skills(args)

    elif args.command == "tasks":
        from umabot.cli.tasks import handle_tasks

        handle_tasks(args)

    elif args.command == "panel":
        from umabot.cli.panel import run_panel

        run_panel(
            config_path=args.config,
            host=args.host,
            port=args.port,
            no_open=args.no_open,
            log_level=args.log_level,
        )

    elif args.command == "control-panel":
        from umabot.cli.control_panel_setup import run_setup

        run_setup(config_path=args.config)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
