"""Configuration read/write API."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from umabot.controlpanel.deps import get_config, get_config_path

router = APIRouter(prefix="/config", tags=["config"])

_MASKED = "••••••••"


@router.get("")
async def get_config_endpoint(config=Depends(get_config)) -> Dict[str, Any]:
    """Return current config, masking secret values."""
    from dataclasses import asdict

    raw = asdict(config)
    _mask_secrets(raw)
    return raw


@router.put("")
async def update_config(
    body: Dict[str, Any],
    config_path: str = Depends(get_config_path),
) -> Dict[str, Any]:
    """Write updated config to YAML and send SIGHUP to gateway if running."""
    cfg_file = Path(config_path)
    if not cfg_file.exists():
        raise HTTPException(status_code=404, detail=f"Config file not found: {config_path}")

    # Load existing raw YAML and deep-merge
    existing = yaml.safe_load(cfg_file.read_text()) or {}
    _deep_merge(existing, body)

    cfg_file.write_text(yaml.dump(existing, default_flow_style=False, allow_unicode=True))

    # Send SIGHUP to gateway if PID file exists
    pid_result = _reload_gateway()
    return {"status": "saved", "reloaded": pid_result}


@router.get("/raw")
async def get_raw_yaml(config_path: str = Depends(get_config_path)) -> Dict[str, Any]:
    """Return raw YAML content."""
    cfg_file = Path(config_path)
    if not cfg_file.exists():
        raise HTTPException(status_code=404, detail="Config file not found")
    return {"yaml": cfg_file.read_text(), "path": config_path}


def _mask_secrets(d: Any) -> None:
    """Recursively mask secret keys in a dict."""
    if not isinstance(d, dict):
        return
    secret_keys = {"api_key", "token", "ws_token", "api_hash", "password", "secret"}
    for k, v in d.items():
        if k in secret_keys and isinstance(v, str) and v:
            d[k] = _MASKED
        else:
            _mask_secrets(v)


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _reload_gateway() -> bool:
    """Send SIGHUP to gateway if PID file exists."""
    from pathlib import Path

    pid_file = Path.home() / ".umabot" / "umabot.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGHUP)
        return True
    except Exception:
        return False
