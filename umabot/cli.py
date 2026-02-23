from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from umabot.config import load_config, parse_override_args, run_wizard
from umabot.skills import SkillRegistry, lint_skill_dir
from umabot.skills.loader import load_skill_metadata


def main() -> None:
    parser = argparse.ArgumentParser(prog="umabot")
    parser.add_argument("--config", dest="config", default=None)
    parser.add_argument(
        "--log-level",
        dest="log_level",
        default=None,
        help="Logging level (DEBUG, INFO, WARNING, ERROR). Can also use UMABOT_LOG_LEVEL.",
    )
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override config key (section.field=value or UMABOT_ENV=value).",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", help="Run configuration wizard")
    subparsers.add_parser("start", help="Start UMA BOT daemon")
    subparsers.add_parser("stop", help="Stop UMA BOT daemon")
    subparsers.add_parser("status", help="Show daemon status")
    subparsers.add_parser("reload", help="Reload daemon configuration")
    orchestrate_parser = subparsers.add_parser("orchestrate", help="Run gateway + connectors together")
    orchestrate_parser.add_argument("--log-level", dest="orchestrate_log_level", default=None)

    channels_parser = subparsers.add_parser("channels", help="Run channel workers")
    channels_sub = channels_parser.add_subparsers(dest="channels_command")
    telegram_worker = channels_sub.add_parser("telegram", help="Run Telegram channel worker")
    telegram_worker.add_argument("--mode", dest="mode", default="channel", choices=["channel", "control"])
    telegram_worker.add_argument("--connector", dest="connector", default="default")
    telegram_worker.add_argument("--token", dest="token", default=None)
    telegram_worker.add_argument("--ws-url", dest="ws_url", default=None)
    telegram_worker.add_argument("--ws-token", dest="ws_token", default=None)
    telegram_user = channels_sub.add_parser("telegram-user", help="Run Telegram user connector worker")
    telegram_user.add_argument("--connector", dest="connector", required=True)
    telegram_user.add_argument("--api-id", dest="api_id", default=None)
    telegram_user.add_argument("--api-hash", dest="api_hash", default=None)
    telegram_user.add_argument("--session-name", dest="session_name", default=None)
    telegram_user.add_argument("--phone", dest="phone", default=None)
    telegram_user.add_argument("--ws-url", dest="ws_url", default=None)
    telegram_user.add_argument("--ws-token", dest="ws_token", default=None)
    telegram_user.add_argument("--login", dest="login", action="store_true")

    skills_parser = subparsers.add_parser("skills", help="Manage skills")
    skills_sub = skills_parser.add_subparsers(dest="skills_command")
    skills_sub.add_parser("list", help="List installed skills")
    install_parser = skills_sub.add_parser("install", help="Install skill from path")
    install_parser.add_argument("path")
    remove_parser = skills_sub.add_parser("remove", help="Remove installed skill")
    remove_parser.add_argument("name")
    lint_parser = skills_sub.add_parser("lint", help="Lint skills")
    lint_parser.add_argument("path", nargs="?")

    args = parser.parse_args()

    if args.command == "init":
        _run_wizard(args.config)
        return
    if args.command == "start":
        _start_daemon(args.config, args.overrides, args.log_level)
        return
    if args.command == "stop":
        _stop_daemon(args.config, args.overrides)
        return
    if args.command == "status":
        _status_daemon(args.config, args.overrides)
        return
    if args.command == "reload":
        _reload_daemon(args.config, args.overrides)
        return
    if args.command == "orchestrate":
        _run_orchestrator(args.config, args.orchestrate_log_level)
        return
    if args.command == "channels":
        _handle_channels(args)
        return
    if args.command == "skills":
        _handle_skills(args)
        return

    parser.print_help()


def _run_wizard(config_path: Optional[str]) -> None:
    run_wizard(config_path)


def _start_daemon(config_path: Optional[str], overrides: list[str], log_level: Optional[str]) -> None:
    override_map = _parse_overrides(overrides)
    config, resolved_path = load_config(config_path=config_path, overrides=override_map)
    pid_file = Path(config.runtime.pid_file)
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    if _pid_running(pid_file):
        print("UMA BOT is already running")
        return

    log_dir = Path(config.runtime.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "umabot.log"

    cmd = [
        sys.executable,
        "-m",
        "umabot.orchestrator",
        "--config",
        str(resolved_path),
        "--log-level",
        log_level or "INFO",
    ]
    with log_file.open("a") as handle:
        proc = subprocess.Popen(
            cmd,
            stdout=handle,
            stderr=handle,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    pid_file.write_text(str(proc.pid))
    print(f"UMA BOT started (PID {proc.pid})")


def _stop_daemon(config_path: Optional[str], overrides: list[str]) -> None:
    override_map = _parse_overrides(overrides)
    config, _ = load_config(config_path=config_path, overrides=override_map)
    pid_file = Path(config.runtime.pid_file)
    pid = _read_pid(pid_file)
    if not pid:
        print("UMA BOT is not running")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        print("UMA BOT was not running")
        return
    _wait_for_exit(pid, pid_file)


def _status_daemon(config_path: Optional[str], overrides: list[str]) -> None:
    override_map = _parse_overrides(overrides)
    config, _ = load_config(config_path=config_path, overrides=override_map)
    pid_file = Path(config.runtime.pid_file)
    pid = _read_pid(pid_file)
    if pid and _is_running(pid):
        print(f"UMA BOT is running (PID {pid})")
        return
    print("UMA BOT is not running")


def _reload_daemon(config_path: Optional[str], overrides: list[str]) -> None:
    override_map = _parse_overrides(overrides)
    config, _ = load_config(config_path=config_path, overrides=override_map)
    pid_file = Path(config.runtime.pid_file)
    pid = _read_pid(pid_file)
    if not pid:
        print("UMA BOT is not running")
        return
    try:
        os.kill(pid, signal.SIGHUP)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        print("UMA BOT is not running")
        return
    print("Reload signal sent")


def _pid_running(pid_file: Path) -> bool:
    pid = _read_pid(pid_file)
    if not pid:
        return False
    if _is_running(pid):
        return True
    pid_file.unlink(missing_ok=True)
    return False


def _read_pid(pid_file: Path) -> Optional[int]:
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except ValueError:
        pid_file.unlink(missing_ok=True)
        return None


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_exit(pid: int, pid_file: Path) -> None:
    for _ in range(20):
        if not _is_running(pid):
            pid_file.unlink(missing_ok=True)
            print("UMA BOT stopped")
            return
        time.sleep(0.5)
    print("UMA BOT stop requested, but process is still running")


def _handle_skills(args) -> None:
    base_dirs = [Path.cwd() / "skills", Path.home() / ".umabot" / "skills"]
    if args.skills_command == "list":
        registry = SkillRegistry()
        registry.load_from_dirs(base_dirs)
        for skill in registry.list():
            print(f"{skill.metadata.name} {skill.metadata.version} - {skill.metadata.description}")
        return

    if args.skills_command == "install":
        source = Path(args.path).expanduser().resolve()
        metadata = load_skill_metadata(source)
        if not metadata:
            print("Invalid skill: missing or invalid SKILL.md")
            return
        target_dir = base_dirs[1] / metadata.name
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source, target_dir)
        print(f"Installed skill {metadata.name}")
        return

    if args.skills_command == "remove":
        target_dir = base_dirs[1] / args.name
        if not target_dir.exists():
            print("Skill not found")
            return
        shutil.rmtree(target_dir)
        print(f"Removed skill {args.name}")
        return

    if args.skills_command == "lint":
        if args.path:
            path = Path(args.path).expanduser().resolve()
            errors = lint_skill_dir(path)
            if errors:
                print("\n".join(errors))
                return
            print("OK")
            return
        registry = SkillRegistry()
        registry.load_from_dirs(base_dirs)
        for skill in registry.list():
            errors = lint_skill_dir(skill.path)
            if errors:
                print(f"{skill.metadata.name}: {', '.join(errors)}")
        print("Lint complete")
        return

    print("Unknown skills command")


def _handle_channels(args) -> None:
    if args.channels_command == "telegram":
        telegram_args = []
        if args.config:
            telegram_args.extend(["--config", args.config])
        if args.token:
            telegram_args.extend(["--token", args.token])
        if args.ws_url:
            telegram_args.extend(["--ws-url", args.ws_url])
        if args.ws_token:
            telegram_args.extend(["--ws-token", args.ws_token])
        if args.mode:
            telegram_args.extend(["--mode", args.mode])
        if args.connector:
            telegram_args.extend(["--connector", args.connector])
        os.execvpe(sys.executable, [sys.executable, "-m", "umabot.connectors.telegram_bot_connector", *telegram_args], os.environ)
        return
    if args.channels_command == "telegram-user":
        telegram_args = []
        if args.config:
            telegram_args.extend(["--config", args.config])
        if args.connector:
            telegram_args.extend(["--connector", args.connector])
        if args.api_id:
            telegram_args.extend(["--api-id", args.api_id])
        if args.api_hash:
            telegram_args.extend(["--api-hash", args.api_hash])
        if args.session_name:
            telegram_args.extend(["--session-name", args.session_name])
        if args.phone:
            telegram_args.extend(["--phone", args.phone])
        if args.ws_url:
            telegram_args.extend(["--ws-url", args.ws_url])
        if args.ws_token:
            telegram_args.extend(["--ws-token", args.ws_token])
        if args.login:
            telegram_args.append("--login")
        os.execvpe(
            sys.executable,
            [sys.executable, "-m", "umabot.connectors.telegram_user_connector", *telegram_args],
            os.environ,
        )
        return
    print("Unknown channels command")


def _run_orchestrator(config_path: Optional[str], log_level: Optional[str]) -> None:
    args = []
    if config_path:
        args.extend(["--config", config_path])
    if log_level:
        args.extend(["--log-level", log_level])
    os.execvpe(sys.executable, [sys.executable, "-m", "umabot.orchestrator", *args], os.environ)


def _parse_overrides(values: list[str]) -> dict:
    if not values:
        return {}
    try:
        return parse_override_args(values)
    except Exception as exc:
        print(f"Invalid --set override: {exc}")
        return {}


if __name__ == "__main__":
    main()
