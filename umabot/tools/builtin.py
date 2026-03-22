from __future__ import annotations

import asyncio
import subprocess
import textwrap
from contextvars import ContextVar
from typing import Any, Dict, Optional

from .registry import RISK_RED, Tool, ToolRegistry, ToolResult

# Per-job skill environment.  The worker sets this before executing any tool
# that was requested while a skill is active.  shell.run reads it and passes
# it as env= to the subprocess so the correct PATH (node, venv, etc.) is used.
# Using a ContextVar means concurrent worker loops each have independent state.
_active_skill_env: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "active_skill_env", default=None
)


def set_active_skill_env(env: Optional[Dict[str, str]]) -> None:
    """Set (or clear) the subprocess env for the current worker job."""
    _active_skill_env.set(env)


def register_builtin_tools(registry: ToolRegistry, *, enable_shell: bool) -> None:
    if enable_shell:
        registry.register(
            Tool(
                name="shell.run",
                schema={
                    "type": "object",
                    "properties": {
                        "cmd": {"type": "string"},
                    },
                    "required": ["cmd"],
                    "additionalProperties": False,
                },
                handler=_shell_run,
                risk_level=RISK_RED,
                description="Run a shell command.",
            )
        )


async def _shell_run(args: Dict[str, Any]) -> ToolResult:
    cmd = args.get("cmd", "")
    if not cmd:
        return ToolResult(content="No command provided.")
    # Strip any embedded null bytes (can appear after JSON round-trips)
    cmd = cmd.replace("\x00", "")
    # Use the skill's resolved env if one is set for this job; otherwise inherit
    # the process environment (None → subprocess inherits os.environ).
    skill_env = _active_skill_env.get()
    import logging as _logging
    _logging.getLogger("umabot.tools.builtin").debug("shell.run cmd_len=%d has_env=%s", len(cmd), skill_env is not None)
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            shell=True,        # enables heredocs, pipes, redirects, &&, ||
            executable="/bin/bash",
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=skill_env,  # None means inherit from process
        )
    except subprocess.TimeoutExpired:
        return ToolResult(content="Command timed out.")
    output = (result.stdout + result.stderr).strip()
    output = textwrap.shorten(output, width=1500, placeholder="...")
    return ToolResult(content=output or "(no output)")
