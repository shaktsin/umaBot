from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from jsonschema import ValidationError, validate

from .registry import SkillRegistry

logger = logging.getLogger("umabot.skills.runtime")


@dataclass
class SkillRunResult:
    ok: bool
    output: str
    data: Optional[Dict[str, Any]] = None


class SkillRuntime:
    """Isolated skill script runtime.

    Scripts are executed as subprocesses inside per-skill virtualenvs.
    """

    def __init__(self, *, skill_registry: SkillRegistry, config) -> None:
        self.skill_registry = skill_registry
        self.config = config

    async def run_script(self, *, skill_name: str, script: str, payload: Dict[str, Any]) -> SkillRunResult:
        logger.debug(
            "Skill runtime request skill=%s script=%s payload_keys=%s",
            skill_name,
            script,
            sorted(list(payload.keys())) if isinstance(payload, dict) else [],
        )
        skill = self.skill_registry.get(skill_name)
        if not skill:
            logger.debug("Skill runtime rejected missing skill=%s", skill_name)
            return SkillRunResult(ok=False, output=f"Skill not found: {skill_name}")
        script_spec = skill.metadata.scripts.get(script)
        if not script_spec:
            logger.debug("Skill runtime rejected undeclared script=%s for skill=%s", script, skill_name)
            return SkillRunResult(ok=False, output=f"Script '{script}' not declared by skill '{skill_name}'")
        if not isinstance(payload, dict):
            payload = {}
        payload = _apply_arg_mapping(payload, script_spec.get("arg_mapping") or {})
        schema = script_spec.get("input_schema") or {}
        if schema:
            try:
                validate(instance=payload, schema=schema)
            except ValidationError as exc:
                detail = {
                    "type": "validation_error",
                    "skill": skill_name,
                    "script": script,
                    "message": exc.message,
                    "path": list(exc.absolute_path),
                    "required": schema.get("required", []),
                }
                logger.debug(
                    "Skill runtime validation failed skill=%s script=%s message=%s",
                    skill_name,
                    script,
                    exc.message,
                )
                return SkillRunResult(
                    ok=False,
                    output=json.dumps(detail, ensure_ascii=True),
                    data=detail,
                )
        script_rel = str(script_spec.get("path", "")).strip()
        script_path = (skill.path / script_rel).resolve()
        if not script_path.exists() or not script_path.is_file():
            logger.debug("Skill runtime missing script file skill=%s script=%s path=%s", skill_name, script, script_path)
            return SkillRunResult(ok=False, output=f"Script file not found: {script_rel}")
        if skill.path.resolve() not in script_path.parents:
            logger.warning("Skill runtime blocked unsafe script path skill=%s path=%s", skill_name, script_path)
            return SkillRunResult(ok=False, output="Unsafe script path")

        # Detect script type and build command
        script_type = self._detect_script_type(script_path)
        cmd = self._build_script_command(skill.path, script_path, script_type)
        if not cmd:
            logger.debug("Skill runtime cannot execute script skill=%s type=%s", skill_name, script_type)
            return SkillRunResult(ok=False, output=f"Unsupported script type: {script_type}")

        runtime_cfg = skill.metadata.runtime or {}
        timeout = int(runtime_cfg.get("timeout_seconds", 20))
        env = self._build_env(skill_name)

        # For bash scripts, pass payload as JSON via stdin
        # For Python scripts, pass payload as JSON via stdin
        stdin_data = json.dumps(
            {
                "input": payload,
                "config": self.config.skill_configs.get(skill_name, {}).get("args", {}),
            }
        ).encode("utf-8")

        proc = await asyncio.to_thread(
            self._spawn_process,
            cmd,
            str(skill.path),
            env,
        )
        logger.debug(
            "Skill runtime spawned pid=%s skill=%s script=%s timeout=%ss",
            getattr(proc, "pid", None),
            skill_name,
            script,
            timeout,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.to_thread(proc.communicate, stdin_data),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Skill runtime timeout skill=%s script=%s timeout=%ss", skill_name, script, timeout)
            _terminate_process(proc)
            return SkillRunResult(ok=False, output=f"Skill script timed out after {timeout}s")
        except Exception as exc:
            logger.exception("Skill runtime execution error skill=%s script=%s", skill_name, script)
            _terminate_process(proc)
            return SkillRunResult(ok=False, output=f"Skill script failed: {exc}")

        out_text = (stdout or b"").decode("utf-8", errors="replace").strip()
        err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            detail = err_text or out_text or f"exit_code={proc.returncode}"
            logger.debug(
                "Skill runtime non-zero exit skill=%s script=%s code=%s detail=%s",
                skill_name,
                script,
                proc.returncode,
                detail,
            )
            return SkillRunResult(ok=False, output=f"Skill script failed: {detail}")
        if not out_text:
            logger.debug("Skill runtime completed with empty output skill=%s script=%s", skill_name, script)
            return SkillRunResult(ok=True, output="")
        try:
            data = json.loads(out_text)
            if isinstance(data, dict):
                message = str(data.get("message") or "")
                logger.debug(
                    "Skill runtime completed skill=%s script=%s message_len=%s has_data=%s",
                    skill_name,
                    script,
                    len(message),
                    bool(data),
                )
                return SkillRunResult(ok=True, output=message, data=data)
        except json.JSONDecodeError:
            pass
        logger.debug(
            "Skill runtime completed skill=%s script=%s plain_output_len=%s",
            skill_name,
            script,
            len(out_text),
        )
        return SkillRunResult(ok=True, output=out_text)

    def _build_env(self, skill_name: str) -> Dict[str, str]:
        env_cfg = self.config.skill_configs.get(skill_name, {}).get("env", {})
        env = {
            "PYTHONUNBUFFERED": "1",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": os.environ.get("PATH", ""),
        }
        for key, value in env_cfg.items():
            env[str(key)] = str(value)
        return env

    def _skill_python(self, skill_path: Path) -> Path:
        if sys.platform == "win32":
            return skill_path / ".venv" / "Scripts" / "python.exe"
        return skill_path / ".venv" / "bin" / "python"

    def _detect_script_type(self, script_path: Path) -> str:
        """Detect script type from extension or shebang."""
        ext = script_path.suffix.lower()

        if ext == ".py":
            return "python"
        elif ext in {".sh", ".bash"}:
            return "bash"

        # Try to detect from shebang
        try:
            with open(script_path, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
                if first_line.startswith("#!"):
                    if "python" in first_line:
                        return "python"
                    elif "bash" in first_line or "sh" in first_line:
                        return "bash"
        except Exception:
            pass

        # Default to python for backward compatibility
        return "python"

    def _build_script_command(self, skill_path: Path, script_path: Path, script_type: str) -> list[str]:
        """Build command to execute script based on type."""
        if script_type == "python":
            python_exe = self._skill_python(skill_path)
            if not python_exe.exists():
                logger.debug("Missing venv python at %s", python_exe)
                return []
            return [str(python_exe), str(script_path)]

        elif script_type == "bash":
            # Use system bash
            bash_exe = "bash"
            if sys.platform == "win32":
                # On Windows, try to find bash (Git Bash, WSL, etc.)
                import shutil as sh
                bash_path = sh.which("bash")
                if not bash_path:
                    logger.warning("Bash not found on Windows")
                    return []
                bash_exe = bash_path
            return [bash_exe, str(script_path)]

        return []

    def _spawn_process(self, cmd: list[str], cwd: str, env: Dict[str, str]) -> subprocess.Popen:
        preexec = _preexec_limits if os.name != "nt" else None
        return subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=preexec,
        )


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        proc.kill()


def _preexec_limits() -> None:
    # Best-effort resource limits for skill subprocesses.
    os.setsid()
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        mem = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    except Exception:
        pass


def _apply_arg_mapping(payload: Dict[str, Any], arg_mapping: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    mapped = dict(payload)
    for target, aliases in arg_mapping.items():
        target_key = str(target).strip()
        if not target_key:
            continue
        existing = mapped.get(target_key)
        if isinstance(existing, str) and existing.strip():
            continue
        if existing not in (None, "", []):
            continue
        alias_list = aliases if isinstance(aliases, list) else [aliases]
        for alias in alias_list:
            alias_key = str(alias).strip()
            if not alias_key:
                continue
            value = mapped.get(alias_key)
            if isinstance(value, str):
                if value.strip():
                    mapped[target_key] = value
                    break
            elif value is not None:
                mapped[target_key] = value
                break
    return mapped
