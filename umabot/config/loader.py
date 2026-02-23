from __future__ import annotations

import getpass
import logging
import os
import platform
import subprocess
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from .schema import Config, default_config


logger = logging.getLogger("umabot.config")

DEFAULT_CONFIG_PATHS = [
    Path.cwd() / "config.yaml",
    Path.home() / ".umabot" / "config.yaml",
]

DEFAULT_ENV_PATHS = [
    Path.cwd() / ".env",
    Path.home() / ".umabot" / ".env",
]

ENV_MAP = {
    "UMABOT_LLM_PROVIDER": ("llm", "provider"),
    "UMABOT_LLM_MODEL": ("llm", "model"),
    "UMABOT_LLM_API_KEY": ("llm", "api_key"),
    "UMABOT_TELEGRAM_TOKEN": ("telegram", "token"),
    "UMABOT_TELEGRAM_ENABLED": ("telegram", "enabled"),
    "UMABOT_DISCORD_TOKEN": ("discord", "token"),
    "UMABOT_DISCORD_ENABLED": ("discord", "enabled"),
    "UMABOT_WHATSAPP_TOKEN": ("whatsapp", "token"),
    "UMABOT_WHATSAPP_ENABLED": ("whatsapp", "enabled"),
    "UMABOT_SHELL_TOOL": ("tools", "shell_enabled"),
    "UMABOT_CONFIRMATION_STRICTNESS": ("policy", "confirmation_strictness"),
    "UMABOT_DB_PATH": ("storage", "db_path"),
    "UMABOT_VAULT_DIR": ("storage", "vault_dir"),
    "UMABOT_PID_FILE": ("runtime", "pid_file"),
    "UMABOT_LOG_DIR": ("runtime", "log_dir"),
    "UMABOT_CONTROL_CHANNEL": ("runtime", "control_channel"),
    "UMABOT_CONTROL_CHAT_ID": ("runtime", "control_chat_id"),
    "UMABOT_CONTROL_CONNECTOR": ("runtime", "control_connector"),
    "UMABOT_WS_HOST": ("runtime", "ws_host"),
    "UMABOT_WS_PORT": ("runtime", "ws_port"),
    "UMABOT_WS_TOKEN": ("runtime", "ws_token"),
}


class ConfigError(RuntimeError):
    pass


def parse_override_args(pairs: list[str]) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}
    for item in pairs:
        if "=" not in item:
            raise ConfigError(f"Invalid override (expected key=value): {item}")
        key, value = item.split("=", 1)
        overrides[key.strip()] = value.strip()
    return overrides


def load_config(
    config_path: Optional[str] = None,
    env_path: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Tuple[Config, str]:
    cfg = default_config()

    resolved_config_path = _pick_config_path(config_path)
    resolved_env_path = _pick_env_path(env_path)

    if resolved_config_path and resolved_config_path.exists():
        data = _read_yaml(resolved_config_path)
        if data:
            _update_dataclass(cfg, data)

    dotenv = _read_dotenv(resolved_env_path)
    if dotenv:
        _apply_env_map(cfg, dotenv)

    _apply_env_map(cfg, os.environ)

    if overrides:
        _apply_overrides(cfg, overrides)

    _load_keychain_secrets(cfg)
    cfg.resolve_paths()

    if not resolved_config_path:
        resolved_config_path = _default_config_path()

    return cfg, str(resolved_config_path)


def save_config(cfg: Config, path: str) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.to_dict()
    _strip_secrets(data)
    target.write_text(yaml.safe_dump(data, sort_keys=False))


def store_secrets(
    *,
    api_key: str = "",
    telegram_token: str = "",
    discord_token: str = "",
) -> None:
    """Persist secrets to macOS Keychain or ~/.umabot/.env fallback."""
    _store_secrets(api_key, telegram_token, discord_token)


def run_wizard(config_path: Optional[str] = None) -> str:
    print("UMA BOT setup wizard")
    provider = _prompt_choice(
        "Select LLM provider", ["openai", "claude", "gemini"], default="openai"
    )
    api_key = getpass.getpass("API key (input hidden): ")
    model = input("Default model (e.g. gpt-4o-mini, claude-3-5-sonnet, gemini-1.5-pro): ").strip()
    telegram_token = getpass.getpass("Telegram bot token (optional): ")
    discord_token = getpass.getpass("Discord bot token (optional): ")
    vault_dir = input("Vault directory [~/.umabot/vault]: ").strip() or "~/.umabot/vault"
    shell_tool = _prompt_yes_no("Enable shell tool? (default OFF)", default=False)
    strictness = _prompt_choice("Confirmation strictness", ["normal", "strict"], default="normal")
    control_channel = _prompt_choice(
        "Owner control channel for confirmations",
        ["telegram", "discord", "none"],
        default="telegram",
    )
    control_chat_id = ""
    control_connector = ""
    if control_channel != "none":
        control_chat_id = input("Owner control chat id (e.g. Telegram chat id): ").strip()
        control_connector = input("Owner control connector name (optional): ").strip()
    ws_token = getpass.getpass("WebSocket token for channel workers (optional): ")

    cfg = default_config()
    cfg.llm.provider = provider
    cfg.llm.model = model or cfg.llm.model
    cfg.telegram.enabled = bool(telegram_token)
    cfg.discord.enabled = bool(discord_token)
    cfg.whatsapp.enabled = False
    cfg.tools.shell_enabled = shell_tool
    cfg.policy.confirmation_strictness = strictness
    cfg.storage.vault_dir = vault_dir
    cfg.runtime.control_channel = control_channel if control_channel != "none" else ""
    cfg.runtime.control_chat_id = control_chat_id or None
    cfg.runtime.control_connector = control_connector or None
    if ws_token:
        cfg.runtime.ws_token = ws_token
    cfg.resolve_paths()

    config_path = config_path or str(_default_config_path())
    save_config(cfg, config_path)

    _store_secrets(api_key, telegram_token, discord_token)
    print(f"Config saved to {config_path}")
    return config_path


def _pick_config_path(config_path: Optional[str]) -> Optional[Path]:
    if config_path:
        return Path(config_path).expanduser()
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate
    return None


def _default_config_path() -> Path:
    return DEFAULT_CONFIG_PATHS[-1]


def _pick_env_path(env_path: Optional[str]) -> Optional[Path]:
    if env_path:
        return Path(env_path).expanduser()
    for candidate in DEFAULT_ENV_PATHS:
        if candidate.exists():
            return candidate
    return DEFAULT_ENV_PATHS[-1]


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc


def _read_dotenv(path: Optional[Path]) -> Dict[str, str]:
    if not path or not path.exists():
        return {}
    data: Dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"')
    return data


def _prompt_choice(prompt: str, choices: list[str], default: str) -> str:
    options = "/".join(choices)
    while True:
        value = input(f"{prompt} [{options}] (default {default}): ").strip().lower()
        if not value:
            return default
        if value in choices:
            return value
        print("Invalid choice.")


def _prompt_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{suffix}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def _apply_env_map(cfg: Config, env: Dict[str, Any]) -> None:
    for key, (section, field) in ENV_MAP.items():
        if key not in env:
            continue
        value = env[key]
        target = getattr(cfg, section)
        if field in {"enabled", "shell_enabled"}:
            value = _parse_bool(value)
        if field in {"ws_port"}:
            try:
                value = int(value)
            except ValueError:
                continue
        if field in {"control_connector"} and value:
            value = str(value)
        setattr(target, field, value)


def _apply_overrides(cfg: Config, overrides: Dict[str, Any]) -> None:
    for key, value in overrides.items():
        if value is None:
            continue
        if key in ENV_MAP:
            section, field = ENV_MAP[key]
        else:
            parts = key.split(".")
            if len(parts) != 2:
                continue
            section, field = parts
        target = getattr(cfg, section, None)
        if not target:
            continue
        if field in {"enabled", "shell_enabled"}:
            value = _parse_bool(value)
        if field in {"ws_port"}:
            try:
                value = int(value)
            except ValueError:
                continue
        if field in {"control_connector"} and value:
            value = str(value)
        setattr(target, field, value)


def _update_dataclass(dc: Any, data: Dict[str, Any]) -> None:
    for key, value in data.items():
        if not hasattr(dc, key):
            continue
        current = getattr(dc, key)
        if is_dataclass(current) and isinstance(value, dict):
            _update_dataclass(current, value)
        else:
            setattr(dc, key, value)


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _strip_secrets(data: Dict[str, Any]) -> None:
    # Remove secrets before writing config.yaml
    if "llm" in data and isinstance(data["llm"], dict):
        data["llm"]["api_key"] = None
    # DEPRECATED: Old telegram/discord/whatsapp sections (kept for backward compat)
    for channel in ("telegram", "discord", "whatsapp"):
        if channel in data and isinstance(data[channel], dict):
            data[channel]["token"] = None
    # NOTE: We keep connector tokens in the config for now
    # TODO: Store connector secrets in keychain/env and load them back
    # if "connectors" in data and isinstance(data["connectors"], list):
    #     for connector in data["connectors"]:
    #         if isinstance(connector, dict):
    #             connector["token"] = None
    #             connector["api_id"] = None
    #             connector["api_hash"] = None


def _store_secrets(api_key: str, telegram_token: str, discord_token: str) -> None:
    if not api_key and not telegram_token and not discord_token:
        return
    if platform.system() == "Darwin":
        stored = True
        if api_key:
            stored = _store_keychain_secret("UMABOT_LLM_API_KEY", api_key) and stored
        if telegram_token:
            stored = _store_keychain_secret("UMABOT_TELEGRAM_TOKEN", telegram_token) and stored
        if discord_token:
            stored = _store_keychain_secret("UMABOT_DISCORD_TOKEN", discord_token) and stored
        if stored:
            return
    _store_env_secrets(api_key, telegram_token, discord_token)


def _store_env_secrets(api_key: str, telegram_token: str, discord_token: str) -> None:
    env_path = DEFAULT_ENV_PATHS[-1]
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if api_key:
        lines.append(f"UMABOT_LLM_API_KEY={api_key}")
    if telegram_token:
        lines.append(f"UMABOT_TELEGRAM_TOKEN={telegram_token}")
    if discord_token:
        lines.append(f"UMABOT_DISCORD_TOKEN={discord_token}")
    env_path.write_text("\n".join(lines) + "\n")
    os.chmod(env_path, 0o600)


def _store_keychain_secret(account: str, secret: str) -> bool:
    try:
        subprocess.run(
            ["security", "add-generic-password", "-a", account, "-s", "umabot", "-w", secret, "-U"],
            check=True,
            capture_output=True,
        )
        return True
    except Exception:
        return False


def _load_keychain_secrets(cfg: Config) -> None:
    if platform.system() != "Darwin":
        return
    debug = _debug_secrets_enabled()
    if not cfg.llm.api_key:
        cfg.llm.api_key = _read_keychain_secret("UMABOT_LLM_API_KEY")
        if debug:
            logger.info("Keychain UMABOT_LLM_API_KEY=%s", cfg.llm.api_key)
    if not cfg.telegram.token:
        cfg.telegram.token = _read_keychain_secret("UMABOT_TELEGRAM_TOKEN")
        if debug:
            logger.info("Keychain UMABOT_TELEGRAM_TOKEN=%s", cfg.telegram.token)
    if not cfg.discord.token:
        cfg.discord.token = _read_keychain_secret("UMABOT_DISCORD_TOKEN")
        if debug:
            logger.info("Keychain UMABOT_DISCORD_TOKEN=%s", cfg.discord.token)


def _read_keychain_secret(account: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", "umabot", "-w"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _debug_secrets_enabled() -> bool:
    return os.environ.get("UMABOT_DEBUG_SECRETS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
