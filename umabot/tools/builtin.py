from __future__ import annotations

import asyncio
import shlex
import subprocess
import textwrap
from typing import Any, Dict

from .registry import RISK_RED, Tool, ToolRegistry, ToolResult


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
    parts = shlex.split(cmd)
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            parts,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(content="Command timed out.")
    output = (result.stdout + result.stderr).strip()
    output = textwrap.shorten(output, width=1500, placeholder="...")
    return ToolResult(content=output or "(no output)")
