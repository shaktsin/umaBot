from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from typing import List, Optional

from umabot.config import load_config


async def _run_process(cmd: List[str], name: str, *, inherit_stdin: bool = False) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *cmd,
        stdout=None,
        stderr=None,
        stdin=None if inherit_stdin else asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )


def _python_cmd() -> str:
    return sys.executable


def _gateway_cmd(config_path: str, log_level: Optional[str]) -> List[str]:
    cmd = [_python_cmd(), "-m", "umabot.gateway", "--config", config_path]
    if log_level:
        cmd.extend(["--log-level", log_level])
    return cmd


def _telegram_bot_cmd(connector: str, config_path: str, log_level: Optional[str]) -> List[str]:
    """Build command for Telegram Bot connector."""
    cmd = [_python_cmd(), "-m", "umabot.connectors.telegram_bot_connector", "--connector", connector, "--config", config_path]
    if log_level:
        cmd.extend(["--log-level", log_level])
    return cmd


def _telegram_user_cmd(connector: str, config_path: str, log_level: Optional[str]) -> List[str]:
    """Build command for Telegram User connector."""
    cmd = [_python_cmd(), "-m", "umabot.connectors.telegram_user_connector", "--connector", connector, "--config", config_path]
    if log_level:
        cmd.extend(["--log-level", log_level])
    return cmd


def _gmail_watch_cmd(connector: str, config_path: str, log_level: Optional[str]) -> List[str]:
    """Build command for Gmail watch connector."""
    cmd = [_python_cmd(), "-m", "umabot.connectors.gmail_connector", "--connector", connector, "--config", config_path]
    if log_level:
        cmd.extend(["--log-level", log_level])
    return cmd


def _build_worker_cmds(cfg, config_path: str, log_level: Optional[str]) -> List[tuple[List[str], bool]]:
    """
    Build commands for all connector workers.

    Control panel connector is included in the connectors list,
    so we don't need special handling for it.

    Returns:
        List of (command, inherit_stdin) tuples
    """
    cmds: List[tuple[List[str], bool]] = []

    for connector in cfg.connectors:
        conn_type = _get_connector_field(connector, "type")
        conn_name = _get_connector_field(connector, "name")

        if not conn_type or not conn_name:
            continue

        if conn_type == "telegram_user":
            cmd = _telegram_user_cmd(conn_name, config_path, log_level)

            # Check if login is allowed for initial setup
            allow_login = _get_connector_field(connector, "allow_login")
            if allow_login and str(allow_login).lower() in {"true", "1", "yes", "y", "on"}:
                cmd.append("--login")
                # Inherit stdin for interactive login
                cmds.append((cmd, True))
            else:
                cmds.append((cmd, False))

        elif conn_type == "telegram_bot":
            cmd = _telegram_bot_cmd(conn_name, config_path, log_level)
            cmds.append((cmd, False))

        elif conn_type == "gmail_imap":
            cmd = _gmail_watch_cmd(conn_name, config_path, log_level)
            cmds.append((cmd, False))

        # elif conn_type == "discord":
        #     cmd = _discord_cmd(conn_name, config_path, log_level)
        #     cmds.append((cmd, False))

    return cmds


def _get_connector_field(connector, field: str) -> Optional[str]:
    if isinstance(connector, dict):
        value = connector.get(field)
    else:
        value = getattr(connector, field, None)
    if value is None:
        return None
    return str(value)


def main(config_path: str | None = None, log_level: str | None = None) -> None:
    if config_path is None and log_level is None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--config", dest="config", default=None)
        parser.add_argument("--log-level", dest="log_level", default=None)
        args = parser.parse_args()
        config_path = args.config
        log_level = args.log_level

    cfg, config_path = load_config(config_path=config_path)

    async def runner() -> None:
        processes: List[asyncio.subprocess.Process] = []
        shutting_down = False

        async def shutdown() -> None:
            nonlocal shutting_down
            if shutting_down:
                return
            shutting_down = True
            for proc in processes:
                if proc.returncode is None:
                    _terminate_process(proc)
            await asyncio.sleep(0)
            for proc in processes:
                if proc.returncode is None:
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        _kill_process(proc)
            await asyncio.gather(*(proc.wait() for proc in processes), return_exceptions=True)

        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _handle_signal():
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)

        processes.append(await _run_process(_gateway_cmd(config_path, log_level), "gateway"))

        for cmd, inherit_stdin in _build_worker_cmds(cfg, config_path, log_level):
            processes.append(await _run_process(cmd, "worker", inherit_stdin=inherit_stdin))

        wait_tasks = [asyncio.create_task(proc.wait()) for proc in processes]
        stop_task = asyncio.create_task(stop_event.wait())
        try:
            done, pending = await asyncio.wait(
                [stop_task, *wait_tasks],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if done and stop_task not in done:
                # A child exited early; stop the whole stack.
                stop_event.set()
            for task in pending:
                task.cancel()
            await shutdown()
        finally:
            await shutdown()

    asyncio.run(runner())


def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    if os.name != "nt" and proc.pid:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            return
        except Exception:
            pass
    proc.terminate()


def _kill_process(proc: asyncio.subprocess.Process) -> None:
    if os.name != "nt" and proc.pid:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except Exception:
            pass
    proc.kill()


if __name__ == "__main__":
    main()
