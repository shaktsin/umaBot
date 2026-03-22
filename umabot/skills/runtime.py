"""Skill runtime provisioning — resolves binary paths and installs dependencies.

Precedence model (highest wins):
  1. config.yaml  skills.<name>   — user per-skill override
  2. config.yaml  skills.defaults — user global defaults
  3. SKILL.md     runtime:        — skill author's declared requirements

At skill load time, SkillRuntimeProvisioner reads each skill's runtime spec,
merges it with the user's config, creates an isolated .venv for Python skills,
and builds the full env dict stored on Skill.resolved_runtime.

The env dict is picked up by shell.run / skills.run_script via a ContextVar
set per-job in the worker.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from umabot.config.schema import SkillRuntimeOverride, SkillsConfig
    from umabot.skills.registry import Skill

logger = logging.getLogger("umabot.skills.runtime")


@dataclass
class ResolvedRuntime:
    """Fully resolved runtime environment for a skill."""

    skill_dir: Path
    python_bin: str      # Absolute path to the Python binary to use
    timeout_seconds: int
    # Complete env dict passed as env= to subprocess calls for this skill.
    # Snapshot of os.environ + PATH additions + skill-specific vars.
    env: Dict[str, str] = field(default_factory=dict)


class SkillRuntimeProvisioner:
    """Provisions skill runtimes at load time.

    Reads ``config.yaml`` skills section and each skill's SKILL.md runtime: spec,
    merges them with the precedence model described above, provisions venvs, and
    returns a ResolvedRuntime ready for subprocess injection.
    """

    def __init__(self, skills_config: "SkillsConfig") -> None:
        self._cfg = skills_config

    def provision(self, skill: "Skill") -> ResolvedRuntime:
        """Provision the runtime for a skill and return a ResolvedRuntime.

        Safe to call on every gateway start — idempotent if nothing changed.
        Errors are logged but never raised (a broken skill env degrades gracefully).
        """
        spec = skill.metadata.runtime          # SKILL.md runtime: section
        skill_dir = skill.path
        defaults = self._cfg.defaults          # config.yaml skills.defaults
        per_skill = self._cfg.get_skill_override(skill.metadata.name)  # config.yaml skills.<name>

        python_bin = self._resolve_python(spec, defaults, per_skill)
        node_bin_dir = self._resolve_node_bin_dir(spec, defaults, per_skill)
        env = self._build_env(spec, skill_dir, python_bin, node_bin_dir, defaults, per_skill)

        # Provision Python venv if requirements declared in SKILL.md
        if spec.requirements:
            req_file = skill_dir / spec.requirements
            if req_file.exists():
                try:
                    venv_python = self._ensure_venv(skill_dir, python_bin, req_file)
                    venv_dir = skill_dir / ".venv"
                    env = self._build_env(spec, skill_dir, venv_python, node_bin_dir, defaults, per_skill)
                    env["VIRTUAL_ENV"] = str(venv_dir)
                    env["PATH"] = str(venv_dir / "bin") + os.pathsep + env.get("PATH", "")
                    python_bin = venv_python
                    logger.info("Skill '%s' venv ready: %s", skill.metadata.name, venv_dir)
                except Exception as exc:
                    logger.warning(
                        "Skill '%s' venv provisioning failed: %s — using system Python",
                        skill.metadata.name, exc,
                    )
            else:
                logger.warning(
                    "Skill '%s' declares requirements=%s but file not found at %s",
                    skill.metadata.name, spec.requirements, req_file,
                )

        return ResolvedRuntime(
            skill_dir=skill_dir,
            python_bin=python_bin,
            timeout_seconds=spec.timeout_seconds,
            env=env,
        )

    # ------------------------------------------------------------------
    # Resolution helpers  (precedence: per_skill > defaults > spec > system)
    # ------------------------------------------------------------------

    def _resolve_python(self, spec, defaults, per_skill) -> str:
        for source in [per_skill, defaults]:
            if source and source.python_bin:
                p = Path(source.python_bin).expanduser()
                if p.exists():
                    return str(p)
        if spec.python_bin:
            p = Path(spec.python_bin).expanduser()
            if p.exists():
                return str(p)
        return sys.executable

    def _resolve_node_bin_dir(self, spec, defaults, per_skill) -> str:
        for source in [per_skill, defaults]:
            if source and source.node_bin:
                p = Path(source.node_bin).expanduser()
                if p.is_dir():
                    return str(p)
        if spec.node_bin:
            p = Path(spec.node_bin).expanduser()
            if p.is_dir():
                return str(p)
        return ""

    def _build_env(self, spec, skill_dir: Path, python_bin: str, node_bin_dir: str,
                   defaults, per_skill) -> Dict[str, str]:
        """Build the full env dict for subprocess calls.

        PATH order (prepended, highest priority first):
          per_skill.extra_path → defaults.extra_path → spec.extra_path → node_bin_dir → python dir

        Env vars order (merged, per_skill wins on conflict):
          defaults.env → spec.env → per_skill.env
        """
        env = os.environ.copy()

        # --- PATH additions ---
        path_prepends: list[str] = []
        for source_paths in [
            per_skill.extra_path if per_skill else [],
            defaults.extra_path,
            spec.extra_path,
        ]:
            for p in source_paths:
                expanded = str(Path(p).expanduser())
                if expanded not in path_prepends:
                    path_prepends.append(expanded)

        if node_bin_dir and node_bin_dir not in path_prepends:
            path_prepends.append(node_bin_dir)

        python_dir = str(Path(python_bin).parent)
        if python_dir not in path_prepends:
            path_prepends.append(python_dir)

        if path_prepends:
            env["PATH"] = os.pathsep.join(path_prepends) + os.pathsep + env.get("PATH", "")

        # --- Env vars (lowest → highest priority) ---
        env.update(defaults.env)
        env.update(spec.env)
        if per_skill:
            env.update(per_skill.env)

        env["SKILL_DIR"] = str(skill_dir)
        return env

    # ------------------------------------------------------------------
    # venv provisioning
    # ------------------------------------------------------------------

    def _ensure_venv(self, skill_dir: Path, python_bin: str, req_file: Path) -> str:
        """Create or update the .venv in skill_dir. Returns path to venv python."""
        venv_dir = skill_dir / ".venv"
        req_hash_file = venv_dir / ".req_hash"
        current_hash = _hash_file(req_file)

        if venv_dir.exists() and req_hash_file.exists():
            if req_hash_file.read_text().strip() == current_hash:
                venv_python = _venv_python(venv_dir)
                if Path(venv_python).exists():
                    return venv_python

        logger.info("Provisioning venv for skill at %s (requirements: %s)", skill_dir, req_file)

        subprocess.run(
            [python_bin, "-m", "venv", str(venv_dir)],
            check=True, capture_output=True, text=True,
        )
        venv_python = _venv_python(venv_dir)
        subprocess.run(
            [venv_python, "-m", "pip", "install", "-r", str(req_file), "--quiet"],
            check=True, capture_output=True, text=True,
        )
        req_hash_file.write_text(current_hash)
        return venv_python


def _venv_python(venv_dir: Path) -> str:
    unix = venv_dir / "bin" / "python"
    if unix.exists():
        return str(unix)
    return str(venv_dir / "Scripts" / "python.exe")


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()
