"""Load and validate Agent Skills (agentskills.io specification)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


# Agent Skills spec: name must be 1-64 chars, lowercase alphanumeric + hyphens,
# no leading/trailing/consecutive hyphens.
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_MAX_NAME_LEN = 64
_MAX_DESC_LEN = 1024


@dataclass
class SkillRuntimeSpec:
    """Runtime requirements declared in SKILL.md frontmatter.

    Example SKILL.md:
        runtime:
          type: python           # python | node | shell (default: shell)
          requirements: requirements.txt   # auto-installed into skill-local .venv
          node_bin: ~/.nvm/versions/node/v20/bin  # overrides global worker.node_bin
          python_bin: ""         # overrides global worker.python_bin
          timeout_seconds: 30
          env:
            MY_VAR: "value"
          extra_path:
            - /usr/local/opt/mytools/bin
    """

    type: str = "shell"                            # python | node | shell
    requirements: str = ""                         # relative path to requirements file
    node_bin: str = ""                             # per-skill node binary dir override
    python_bin: str = ""                           # per-skill python binary override
    timeout_seconds: int = 30
    env: Dict[str, str] = field(default_factory=dict)
    extra_path: List[str] = field(default_factory=list)


@dataclass
class SkillMetadata:
    """Metadata parsed from SKILL.md YAML frontmatter (Agent Skills spec)."""

    name: str
    description: str
    license: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    body: str = ""  # Markdown instructions body (after frontmatter)
    runtime: SkillRuntimeSpec = field(default_factory=SkillRuntimeSpec)


def load_skill_metadata(skill_dir: Path) -> Optional[SkillMetadata]:
    """Load and validate SKILL.md from a skill directory."""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None
    text = skill_file.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)
    if frontmatter is None:
        return None
    return _validate_metadata(frontmatter, body)


def _parse_frontmatter(text: str) -> tuple[Optional[Dict[str, Any]], str]:
    """Parse YAML frontmatter and return (data, body).

    Returns (None, "") if frontmatter is missing or malformed.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, ""
    # Find closing ---
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, ""
    yaml_block = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :]).strip()
    data = yaml.safe_load(yaml_block) or {}
    return data, body


def _validate_metadata(data: Dict[str, Any], body: str) -> SkillMetadata:
    """Validate frontmatter against Agent Skills spec."""
    # Required fields
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Missing required field: name")
    if len(name) > _MAX_NAME_LEN:
        raise ValueError(f"name exceeds {_MAX_NAME_LEN} characters")
    if not _NAME_RE.match(name) or "--" in name:
        raise ValueError(
            f"Invalid name '{name}': must be lowercase alphanumeric + hyphens, "
            "no leading/trailing/consecutive hyphens"
        )

    description = str(data.get("description", "")).strip()
    if not description:
        raise ValueError("Missing required field: description")
    if len(description) > _MAX_DESC_LEN:
        raise ValueError(f"description exceeds {_MAX_DESC_LEN} characters")

    # Optional fields
    license_str = str(data.get("license", "")).strip()
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    # Optional runtime spec
    runtime_data = data.get("runtime") or {}
    if not isinstance(runtime_data, dict):
        runtime_data = {}
    runtime = _parse_runtime_spec(runtime_data)

    return SkillMetadata(
        name=name,
        description=description,
        license=license_str,
        metadata=metadata,
        body=body,
        runtime=runtime,
    )


def _parse_runtime_spec(data: Dict[str, Any]) -> SkillRuntimeSpec:
    """Parse runtime: section from SKILL.md frontmatter."""
    env = data.get("env") or {}
    if not isinstance(env, dict):
        env = {}
    extra_path = data.get("extra_path") or []
    if not isinstance(extra_path, list):
        extra_path = [str(extra_path)]

    timeout = data.get("timeout_seconds", 30)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 30

    return SkillRuntimeSpec(
        type=str(data.get("type", "shell")).lower(),
        requirements=str(data.get("requirements", "")),
        node_bin=str(data.get("node_bin", "")),
        python_bin=str(data.get("python_bin", "")),
        timeout_seconds=timeout,
        env={str(k): str(v) for k, v in env.items()},
        extra_path=[str(p) for p in extra_path],
    )
