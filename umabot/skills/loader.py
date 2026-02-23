from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


@dataclass
class SkillMetadata:
    name: str
    version: str
    description: str
    allowed_tools: list[str]
    risk_level: str
    triggers: list[str]
    scripts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    install_config: Dict[str, Any] = field(default_factory=dict)
    runtime: Dict[str, Any] = field(default_factory=dict)


def load_skill_metadata(skill_dir: Path) -> Optional[SkillMetadata]:
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None
    text = skill_file.read_text()
    data = _parse_frontmatter(text)
    if not data:
        return None
    return _validate_metadata(data)


def _parse_frontmatter(text: str) -> Optional[Dict[str, Any]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end_idx = lines[1:].index("---") + 1
    except ValueError:
        return None
    yaml_block = "\n".join(lines[1:end_idx])
    return yaml.safe_load(yaml_block) or {}


def _validate_metadata(data: Dict[str, Any]) -> SkillMetadata:
    required = ["name", "version", "description", "allowed_tools", "risk_level", "triggers"]
    for key in required:
        if key not in data:
            raise ValueError(f"Missing field: {key}")
    risk = str(data["risk_level"]).lower()
    if risk not in {"green", "yellow", "red"}:
        raise ValueError("Invalid risk_level")
    return SkillMetadata(
        name=str(data["name"]),
        version=str(data["version"]),
        description=str(data["description"]),
        allowed_tools=list(data["allowed_tools"] or []),
        risk_level=risk.upper(),
        triggers=list(data["triggers"] or []),
        scripts=_validate_scripts(data.get("scripts")),
        install_config=_validate_install_config(data.get("install_config")),
        runtime=_validate_runtime(data.get("runtime")),
    )


def _validate_scripts(raw: Any) -> Dict[str, Dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("scripts must be a mapping")
    scripts: Dict[str, Dict[str, Any]] = {}
    for name, script_def in raw.items():
        script_name = str(name).strip()
        if isinstance(script_def, dict):
            script_path = str(script_def.get("path", "")).strip()
            description = str(script_def.get("description", "")).strip()
            input_schema = script_def.get("input_schema")
            arg_mapping = script_def.get("arg_mapping") or {}
            when = script_def.get("when") or {}
            examples = script_def.get("examples") or []
        else:
            script_path = str(script_def).strip()
            description = ""
            input_schema = None
            arg_mapping = {}
            when = {}
            examples = []
        if not script_name or not script_path:
            raise ValueError("scripts entries must have non-empty name and path")
        if Path(script_path).is_absolute() or ".." in Path(script_path).parts:
            raise ValueError(f"scripts[{script_name}] must be a safe relative path")
        if input_schema is not None and not isinstance(input_schema, dict):
            raise ValueError(f"scripts[{script_name}].input_schema must be an object")
        if not isinstance(arg_mapping, dict):
            raise ValueError(f"scripts[{script_name}].arg_mapping must be an object")
        normalized_mapping: Dict[str, list[str]] = {}
        for target_key, aliases in arg_mapping.items():
            if isinstance(aliases, list):
                normalized_mapping[str(target_key)] = [str(a) for a in aliases]
            else:
                normalized_mapping[str(target_key)] = [str(aliases)]
        scripts[script_name] = {
            "path": script_path,
            "description": description,
            "input_schema": input_schema or {},
            "arg_mapping": normalized_mapping,
            "when": when if isinstance(when, dict) else {},
            "examples": examples if isinstance(examples, list) else [],
        }
    return scripts


def _validate_install_config(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {"args": {}, "env": {}}
    if not isinstance(raw, dict):
        raise ValueError("install_config must be a mapping")
    args = raw.get("args", {})
    env = raw.get("env", {})
    if not isinstance(args, dict) or not isinstance(env, dict):
        raise ValueError("install_config.args/env must be mappings")
    return {"args": args, "env": env}


def _validate_runtime(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {"timeout_seconds": 20}
    if not isinstance(raw, dict):
        raise ValueError("runtime must be a mapping")
    timeout = raw.get("timeout_seconds", 20)
    try:
        timeout_int = int(timeout)
    except Exception as exc:
        raise ValueError("runtime.timeout_seconds must be an integer") from exc
    if timeout_int <= 0:
        raise ValueError("runtime.timeout_seconds must be > 0")
    return {"timeout_seconds": timeout_int}
