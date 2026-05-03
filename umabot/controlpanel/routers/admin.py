"""Admin configuration APIs for LLM providers, agents, and MCP servers."""

from __future__ import annotations

import os
import signal
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from umabot.config import load_config, store_provider_api_key
from umabot.config.schema import default_config
from umabot.controlpanel.deps import get_config_path
from umabot.tools.mcp_registry import MCPRegistry

router = APIRouter(prefix="/admin", tags=["admin"])

_MASKED = "••••••••"


class ProviderUpdate(BaseModel):
    enabled: Optional[bool] = None
    models: Optional[List[str]] = None
    default_model: Optional[str] = None
    api_key: Optional[str] = None
    set_active: bool = False
    active_model: Optional[str] = None


class AgentsUpdate(BaseModel):
    enabled: bool


class MCPServerCreate(BaseModel):
    name: str
    transport: str = "stdio"
    command: str = ""
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    env_vars: List[str] = Field(default_factory=list)
    cwd: str = ""
    url: str = ""
    bearer_token_env_var: str = ""
    http_headers: Dict[str, str] = Field(default_factory=dict)
    env_http_headers: Dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    required: bool = False
    startup_timeout_sec: float = 10.0
    tool_timeout_sec: float = 60.0
    enabled_tools: List[str] = Field(default_factory=list)
    disabled_tools: List[str] = Field(default_factory=list)
    mcp_oauth_callback_port: int = 0
    mcp_oauth_callback_url: str = ""


class MCPServerPatch(BaseModel):
    transport: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    env_vars: Optional[List[str]] = None
    cwd: Optional[str] = None
    url: Optional[str] = None
    bearer_token_env_var: Optional[str] = None
    http_headers: Optional[Dict[str, str]] = None
    env_http_headers: Optional[Dict[str, str]] = None
    enabled: Optional[bool] = None
    required: Optional[bool] = None
    startup_timeout_sec: Optional[float] = None
    tool_timeout_sec: Optional[float] = None
    enabled_tools: Optional[List[str]] = None
    disabled_tools: Optional[List[str]] = None
    mcp_oauth_callback_port: Optional[int] = None
    mcp_oauth_callback_url: Optional[str] = None


@router.get("/llm-providers")
async def get_llm_providers(config_path: str = Depends(get_config_path)) -> Dict[str, Any]:
    cfg, _ = load_config(config_path=config_path)
    provider_map = getattr(cfg, "llm_providers", {}) or {}
    items = []
    for name, provider_cfg in sorted(provider_map.items()):
        enabled = bool(getattr(provider_cfg, "enabled", True))
        models = [m for m in (getattr(provider_cfg, "models", []) or []) if isinstance(m, str)]
        default_model = str(getattr(provider_cfg, "default_model", "") or "")
        has_key = bool(getattr(provider_cfg, "api_key", None))
        items.append(
            {
                "name": name,
                "label": _provider_label(name),
                "enabled": enabled,
                "models": models,
                "default_model": default_model,
                "api_key": _MASKED if has_key else "",
                "api_key_configured": has_key,
                "active": cfg.llm.provider.lower() == name.lower(),
            }
        )
    return {
        "active_provider": cfg.llm.provider,
        "active_model": cfg.llm.model,
        "agents_enabled": bool(getattr(getattr(cfg, "agents", None), "enabled", False)),
        "providers": items,
    }


@router.put("/llm-providers/{provider_name}")
async def update_llm_provider(
    provider_name: str,
    body: ProviderUpdate,
    config_path: str = Depends(get_config_path),
) -> Dict[str, Any]:
    provider_name = provider_name.strip().lower()
    raw = _load_raw_yaml(config_path)
    _ensure_provider_defaults(raw)

    providers = raw.setdefault("llm_providers", {})
    if provider_name not in providers:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_name}")

    provider_cfg = providers[provider_name]
    if not isinstance(provider_cfg, dict):
        provider_cfg = {}
        providers[provider_name] = provider_cfg

    if body.enabled is not None:
        provider_cfg["enabled"] = bool(body.enabled)
    if body.models is not None:
        provider_cfg["models"] = _normalize_models(body.models)
    if body.default_model is not None:
        provider_cfg["default_model"] = (body.default_model or "").strip()

    incoming_key = (body.api_key or "").strip()
    if incoming_key and incoming_key != _MASKED:
        store_provider_api_key(provider_name, incoming_key)
    provider_cfg["api_key"] = None

    llm = raw.setdefault("llm", {})
    if not isinstance(llm, dict):
        llm = {}
        raw["llm"] = llm

    if body.set_active:
        llm["provider"] = provider_name
        selected_model = (
            (body.active_model or "").strip()
            or str(provider_cfg.get("default_model", "") or "").strip()
            or str(llm.get("model", "") or "").strip()
        )
        if selected_model:
            llm["model"] = selected_model
    elif body.active_model is not None and str(llm.get("provider", "")).lower() == provider_name:
        llm["model"] = (body.active_model or "").strip()

    _write_raw_yaml(config_path, raw)
    reloaded = _reload_gateway()
    return {"status": "saved", "reloaded": reloaded}


@router.put("/agents")
async def update_agents(
    body: AgentsUpdate,
    config_path: str = Depends(get_config_path),
) -> Dict[str, Any]:
    raw = _load_raw_yaml(config_path)
    agents = raw.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        raw["agents"] = agents
    agents["enabled"] = bool(body.enabled)
    _write_raw_yaml(config_path, raw)
    return {"status": "saved", "reloaded": _reload_gateway()}


@router.get("/mcp-servers")
async def get_mcp_servers(config_path: str = Depends(get_config_path)) -> Dict[str, Any]:
    cfg, _ = load_config(config_path=config_path)
    servers = []
    for server in getattr(cfg, "mcp_servers", []) or []:
        servers.append(_mcp_server_to_dict(server))
    return {"servers": servers}


@router.post("/mcp-servers")
async def create_mcp_server(
    body: MCPServerCreate,
    config_path: str = Depends(get_config_path),
) -> Dict[str, Any]:
    raw = _load_raw_yaml(config_path)
    servers = raw.setdefault("mcp_servers", [])
    if not isinstance(servers, list):
        raise HTTPException(status_code=400, detail="mcp_servers must be a list")

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="MCP server name is required")
    if any(isinstance(item, dict) and str(item.get("name", "")) == name for item in servers):
        raise HTTPException(status_code=409, detail=f"MCP server '{name}' already exists")

    payload = _normalized_mcp_payload(body.model_dump())
    _validate_mcp_payload(payload)
    servers.append(payload)

    _write_raw_yaml(config_path, raw)
    return {"status": "saved", "reloaded": _reload_gateway()}


@router.put("/mcp-servers/{server_name}")
async def update_mcp_server(
    server_name: str,
    body: MCPServerPatch,
    config_path: str = Depends(get_config_path),
) -> Dict[str, Any]:
    raw = _load_raw_yaml(config_path)
    servers = raw.get("mcp_servers")
    if not isinstance(servers, list):
        raise HTTPException(status_code=404, detail="No MCP servers configured")

    found = False
    for server in servers:
        if isinstance(server, dict) and str(server.get("name", "")) == server_name:
            merged = dict(server)
            merged.update({k: v for k, v in body.model_dump(exclude_none=True).items()})
            normalized = _normalized_mcp_payload(merged)
            _validate_mcp_payload(normalized)
            server.clear()
            server.update(normalized)
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {server_name}")

    _write_raw_yaml(config_path, raw)
    return {"status": "saved", "reloaded": _reload_gateway()}


@router.delete("/mcp-servers/{server_name}")
async def delete_mcp_server(
    server_name: str,
    config_path: str = Depends(get_config_path),
) -> Dict[str, Any]:
    raw = _load_raw_yaml(config_path)
    servers = raw.get("mcp_servers")
    if not isinstance(servers, list):
        raise HTTPException(status_code=404, detail="No MCP servers configured")

    filtered = [item for item in servers if not (isinstance(item, dict) and str(item.get("name", "")) == server_name)]
    if len(filtered) == len(servers):
        raise HTTPException(status_code=404, detail=f"MCP server not found: {server_name}")
    raw["mcp_servers"] = filtered
    _write_raw_yaml(config_path, raw)
    return {"status": "saved", "reloaded": _reload_gateway()}


@router.post("/mcp-servers/{server_name}/test")
async def test_mcp_server(
    server_name: str,
    config_path: str = Depends(get_config_path),
) -> Dict[str, Any]:
    cfg, _ = load_config(config_path=config_path)
    target = _find_mcp_server(cfg, server_name)
    if target is None:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {server_name}")

    target.enabled = True
    registry = MCPRegistry([target])
    try:
        await registry.start()
        discovered = registry.get_all_tools()
        methods = _discovered_methods(server_name, discovered)
        return {
            "server": server_name,
            "ok": True,
            "method_count": len(methods),
            "methods": methods,
        }
    except Exception as exc:
        return {
            "server": server_name,
            "ok": False,
            "method_count": 0,
            "methods": [],
            "error": str(exc),
        }
    finally:
        await registry.stop()


@router.get("/mcp-servers/{server_name}/methods")
async def get_mcp_server_methods(
    server_name: str,
    config_path: str = Depends(get_config_path),
) -> Dict[str, Any]:
    cfg, _ = load_config(config_path=config_path)
    target = _find_mcp_server(cfg, server_name)

    if target is None:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {server_name}")

    target.enabled = True
    registry = MCPRegistry([target])
    try:
        await registry.start()
        discovered = registry.get_all_tools()
    except Exception as exc:
        return {"server": server_name, "discovered": False, "methods": [], "error": str(exc)}
    finally:
        await registry.stop()

    methods = _discovered_methods(server_name, discovered)
    return {"server": server_name, "discovered": True, "methods": methods}


def _provider_label(name: str) -> str:
    mapping = {"openai": "OpenAI", "claude": "Claude", "gemini": "Gemini"}
    return mapping.get(name.lower(), name)


def _find_mcp_server(cfg: Any, server_name: str):
    for server in getattr(cfg, "mcp_servers", []) or []:
        if server.name == server_name:
            return deepcopy(server)
    return None


def _discovered_methods(server_name: str, discovered: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    methods = []
    for prefixed_name, tool in discovered.items():
        if tool.get("server") != server_name:
            continue
        methods.append(
            {
                "name": tool.get("tool", ""),
                "prefixed_name": prefixed_name,
                "description": tool.get("description", ""),
                "schema": tool.get("schema", {}),
            }
        )
    methods.sort(key=lambda item: item["name"])
    return methods


def _mcp_server_to_dict(server: Any) -> Dict[str, Any]:
    return {
        "name": server.name,
        "transport": server.transport,
        "command": server.command,
        "args": list(getattr(server, "args", []) or []),
        "env": dict(getattr(server, "env", {}) or {}),
        "env_vars": list(getattr(server, "env_vars", []) or []),
        "cwd": str(getattr(server, "cwd", "") or ""),
        "url": str(getattr(server, "url", "") or ""),
        "bearer_token_env_var": str(getattr(server, "bearer_token_env_var", "") or ""),
        "http_headers": dict(getattr(server, "http_headers", {}) or {}),
        "env_http_headers": dict(getattr(server, "env_http_headers", {}) or {}),
        "enabled": bool(getattr(server, "enabled", True)),
        "required": bool(getattr(server, "required", False)),
        "startup_timeout_sec": float(getattr(server, "startup_timeout_sec", 10.0) or 10.0),
        "tool_timeout_sec": float(getattr(server, "tool_timeout_sec", 60.0) or 60.0),
        "enabled_tools": list(getattr(server, "enabled_tools", []) or []),
        "disabled_tools": list(getattr(server, "disabled_tools", []) or []),
        "mcp_oauth_callback_port": int(getattr(server, "mcp_oauth_callback_port", 0) or 0),
        "mcp_oauth_callback_url": str(getattr(server, "mcp_oauth_callback_url", "") or ""),
    }


def _normalized_mcp_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload)
    data["name"] = str(data.get("name", "")).strip()
    data["transport"] = str(data.get("transport", "stdio") or "stdio").strip().lower()
    data["command"] = str(data.get("command", "") or "").strip()
    data["args"] = [str(v).strip() for v in (data.get("args") or []) if str(v).strip()]
    data["env"] = {str(k): str(v) for k, v in dict(data.get("env") or {}).items() if str(k).strip()}
    data["env_vars"] = [str(v).strip() for v in (data.get("env_vars") or []) if str(v).strip()]
    data["cwd"] = str(data.get("cwd", "") or "").strip()
    data["url"] = str(data.get("url", "") or "").strip()
    data["bearer_token_env_var"] = str(data.get("bearer_token_env_var", "") or "").strip()
    data["http_headers"] = {
        str(k): str(v) for k, v in dict(data.get("http_headers") or {}).items() if str(k).strip()
    }
    data["env_http_headers"] = {
        str(k): str(v) for k, v in dict(data.get("env_http_headers") or {}).items() if str(k).strip()
    }
    data["enabled"] = bool(data.get("enabled", True))
    data["required"] = bool(data.get("required", False))
    data["startup_timeout_sec"] = float(data.get("startup_timeout_sec", 10.0) or 10.0)
    data["tool_timeout_sec"] = float(data.get("tool_timeout_sec", 60.0) or 60.0)
    data["enabled_tools"] = _normalize_models([str(v) for v in (data.get("enabled_tools") or [])])
    data["disabled_tools"] = _normalize_models([str(v) for v in (data.get("disabled_tools") or [])])
    data["mcp_oauth_callback_port"] = int(data.get("mcp_oauth_callback_port", 0) or 0)
    data["mcp_oauth_callback_url"] = str(data.get("mcp_oauth_callback_url", "") or "").strip()
    return data


def _validate_mcp_payload(payload: Dict[str, Any]) -> None:
    if not payload.get("name"):
        raise HTTPException(status_code=400, detail="MCP server name is required")
    transport = payload.get("transport")
    if transport not in {"stdio", "http"}:
        raise HTTPException(status_code=400, detail="MCP transport must be 'stdio' or 'http'")
    if transport == "stdio" and not payload.get("command"):
        raise HTTPException(status_code=400, detail="MCP stdio server requires 'command'")
    if transport == "http" and not payload.get("url"):
        raise HTTPException(status_code=400, detail="MCP http server requires 'url'")
    if float(payload.get("startup_timeout_sec", 0) or 0) <= 0:
        raise HTTPException(status_code=400, detail="startup_timeout_sec must be > 0")
    if float(payload.get("tool_timeout_sec", 0) or 0) <= 0:
        raise HTTPException(status_code=400, detail="tool_timeout_sec must be > 0")


def _normalize_models(models: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for model in models:
        value = (model or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _provider_defaults() -> Dict[str, Dict[str, Any]]:
    providers = default_config().llm_providers
    return {name: asdict(cfg) for name, cfg in providers.items()}


def _ensure_provider_defaults(raw: Dict[str, Any]) -> None:
    providers = raw.setdefault("llm_providers", {})
    if not isinstance(providers, dict):
        providers = {}
        raw["llm_providers"] = providers
    defaults = _provider_defaults()
    for name, default_cfg in defaults.items():
        existing = providers.get(name)
        if not isinstance(existing, dict):
            providers[name] = dict(default_cfg)
            continue
        merged = dict(default_cfg)
        merged.update(existing)
        providers[name] = merged


def _load_raw_yaml(config_path: str) -> Dict[str, Any]:
    cfg_file = Path(config_path)
    if not cfg_file.exists():
        raise HTTPException(status_code=404, detail=f"Config file not found: {config_path}")
    return yaml.safe_load(cfg_file.read_text()) or {}


def _write_raw_yaml(config_path: str, raw: Dict[str, Any]) -> None:
    cfg_file = Path(config_path)
    cfg_file.write_text(yaml.dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False))


def _reload_gateway() -> bool:
    pid_file = Path.home() / ".umabot" / "umabot.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGHUP)
        return True
    except Exception:
        return False
