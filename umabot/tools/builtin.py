from __future__ import annotations

import asyncio
import subprocess
import textwrap
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, List, Optional

from .registry import RISK_RED, RISK_YELLOW, Tool, ToolRegistry, ToolResult
from .shell_env import apply_zsh_path, merge_path_segments

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


def register_builtin_tools(
    registry: ToolRegistry,
    *,
    enable_shell: bool,
    skill_registry=None,
    workspaces: Optional[List] = None,
) -> None:
    if enable_shell:
        registry.register(
            Tool(
                name="shell.run",
                schema={
                    "type": "object",
                    "properties": {
                        "cmd": {"type": "string"},
                        "cwd": {
                            "type": "string",
                            "description": (
                                "Working directory for the command. "
                                "Defaults to the active workspace path when one is set."
                            ),
                        },
                    },
                    "required": ["cmd"],
                    "additionalProperties": False,
                },
                handler=_shell_run,
                risk_level=RISK_RED,
                description=(
                    "Run a shell command. When a workspace is active the cwd "
                    "defaults to the workspace path and the ACL shell flag must be true."
                ),
            )
        )

    # File tools — only registered when at least one workspace is configured
    # so agents know they can use them.
    registry.register(
        Tool(
            name="file.write",
            schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "File path to write. Relative paths are resolved "
                            "against the active workspace root."
                        ),
                    },
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=_file_write,
            risk_level=RISK_YELLOW,
            description=(
                "Write text content to a file inside the active workspace. "
                "Creates parent directories as needed. Requires an active workspace."
            ),
        )
    )

    registry.register(
        Tool(
            name="file.read",
            schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to read (relative to workspace root or absolute).",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=_file_read,
            risk_level=RISK_YELLOW,
            description="Read a text file from the active workspace.",
        )
    )

    registry.register(
        Tool(
            name="file.list",
            schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory to list (default: workspace root).",
                    },
                },
                "additionalProperties": False,
            },
            handler=_file_list,
            risk_level=RISK_YELLOW,
            description="List files and directories inside the active workspace.",
        )
    )

    registry.register(
        Tool(
            name="file.delete",
            schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to delete (relative to workspace root or absolute).",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=_file_delete,
            risk_level=RISK_RED,
            description="Delete a file from the active workspace. Requires acl.delete_files=true.",
        )
    )

    # Stage-3 progressive disclosure: LLM calls this when it needs the full
    # SKILL.md body (not just the summary injected at stage 2).
    if skill_registry is not None:
        registry.register(
            Tool(
                name="skill.get_instructions",
                schema={
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "Name of the skill to retrieve full instructions for.",
                        },
                    },
                    "required": ["skill_name"],
                    "additionalProperties": False,
                },
                handler=_make_skill_instructions_handler(skill_registry),
                description=(
                    "Retrieve the full instructions for a skill by name. "
                    "Use this when the skill summary in your context is not enough "
                    "to complete the task."
                ),
            )
        )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _make_skill_instructions_handler(skill_registry):
    async def _skill_get_instructions(args: Dict[str, Any]) -> ToolResult:
        name = args.get("skill_name", "").strip()
        if not name:
            return ToolResult(content="skill_name is required.")
        skill = skill_registry.get(name)
        if not skill:
            available = ", ".join(s.metadata.name for s in skill_registry.list())
            return ToolResult(
                content=f"Skill '{name}' not found. Available skills: {available}"
            )
        body = skill.metadata.body or ""
        if not body:
            return ToolResult(content=f"Skill '{name}' has no instructions.")
        return ToolResult(
            content=(
                f"Full instructions for skill '{name}':\n"
                f"Skill directory: {skill.path}\n\n"
                f"{body}"
            )
        )

    return _skill_get_instructions


async def _shell_run(args: Dict[str, Any]) -> ToolResult:
    import logging as _logging
    from umabot.tools.workspace import get_active_workspace

    cmd = args.get("cmd", "")
    if not cmd:
        return ToolResult(content="No command provided.")
    cmd = cmd.replace("\x00", "")

    import os as _os
    skill_env = _active_skill_env.get()
    env = dict(_os.environ)
    if skill_env is not None:
        env.update(skill_env)
        # Always keep base PATH segments reachable when a skill sets a custom PATH.
        env["PATH"] = merge_path_segments(skill_env.get("PATH", ""), _os.environ.get("PATH", ""))
    # Also layer in PATH discovered from login zsh so npm/npx from ~/.zshrc are available.
    env = apply_zsh_path(env)
    ws = get_active_workspace()

    # Resolve cwd: explicit arg > active workspace > None (inherit process cwd)
    explicit_cwd = (args.get("cwd") or "").strip() or None
    cwd: Optional[str] = None
    if explicit_cwd:
        candidate = Path(explicit_cwd).expanduser()
        if not candidate.is_absolute() and ws:
            # Relative paths are always anchored to the active workspace root,
            # never to the gateway process CWD (which is the umabot source dir).
            candidate = Path(ws.path).expanduser().resolve() / candidate
        cwd = str(candidate.resolve())
        Path(cwd).mkdir(parents=True, exist_ok=True)
    elif ws:
        if not ws.acl.shell:
            return ToolResult(
                content=f"Workspace '{ws.name}' does not allow shell commands (acl.shell=false)."
            )
        cwd = str(Path(ws.path).expanduser().resolve())
        Path(cwd).mkdir(parents=True, exist_ok=True)

    _logging.getLogger("umabot.tools.builtin").debug(
        "shell.run cmd_len=%d has_env=%s cwd=%s", len(cmd), skill_env is not None, cwd
    )
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            shell=True,
            executable="/bin/bash",
            capture_output=True,
            timeout=60,
            check=False,
            env=env,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            content="Command timed out.",
            data={
                "cmd": cmd,
                "cwd": cwd or "",
                "exit_code": 124,
                "timed_out": True,
            },
        )
    stdout = result.stdout.decode("utf-8", errors="replace") if isinstance(result.stdout, bytes) else (result.stdout or "")
    stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else (result.stderr or "")
    output = (stdout + stderr).strip()
    output = textwrap.shorten(output, width=1500, placeholder="...")
    return ToolResult(
        content=output or "(no output)",
        data={
            "cmd": cmd,
            "cwd": cwd or "",
            "exit_code": int(result.returncode),
            "timed_out": False,
        },
    )


async def _file_write(args: Dict[str, Any]) -> ToolResult:
    from umabot.tools.workspace import get_active_workspace, enforce_path

    ws = get_active_workspace()
    if not ws:
        return ToolResult(content="No active workspace. Set a workspace before writing files.")

    path_str = args.get("path", "").strip()
    content = args.get("content", "")
    if not path_str:
        return ToolResult(content="path is required.")

    try:
        ws_root = Path(ws.path).expanduser().resolve()
        candidate = Path(path_str).expanduser()
        if not candidate.is_absolute():
            candidate = ws_root / candidate
        resolved = candidate.resolve()
        resolved.relative_to(ws_root)
    except ValueError:
        return ToolResult(content=f"Path '{path_str}' is outside workspace '{ws.name}'.")

    is_new = not resolved.exists()
    try:
        if is_new:
            if not ws.acl.create_files:
                return ToolResult(
                    content=f"Workspace '{ws.name}' does not allow creating new files (acl.create_files=false)."
                )
        else:
            if not ws.acl.write:
                return ToolResult(
                    content=f"Workspace '{ws.name}' does not allow modifying files (acl.write=false)."
                )
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return ToolResult(content=f"Wrote {len(content)} chars to {resolved}")
    except Exception as exc:
        return ToolResult(content=f"Write failed: {exc}")


async def _file_read(args: Dict[str, Any]) -> ToolResult:
    from umabot.tools.workspace import get_active_workspace

    ws = get_active_workspace()
    if not ws:
        return ToolResult(content="No active workspace. Set a workspace before reading files.")

    path_str = args.get("path", "").strip()
    if not path_str:
        return ToolResult(content="path is required.")

    try:
        ws_root = Path(ws.path).expanduser().resolve()
        candidate = Path(path_str).expanduser()
        if not candidate.is_absolute():
            candidate = ws_root / candidate
        resolved = candidate.resolve()
        resolved.relative_to(ws_root)
    except ValueError:
        return ToolResult(content=f"Path '{path_str}' is outside workspace '{ws.name}'.")

    if not ws.acl.read:
        return ToolResult(content=f"Workspace '{ws.name}' does not allow reads (acl.read=false).")
    if not resolved.exists():
        return ToolResult(content=f"File not found: {resolved}")
    if resolved.is_dir():
        return ToolResult(content=f"'{resolved}' is a directory. Use file.list to browse.")
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
        if len(text) > 12000:
            text = text[:12000] + f"\n... [truncated — {len(text)} total chars]"
        return ToolResult(content=text)
    except Exception as exc:
        return ToolResult(content=f"Read failed: {exc}")


async def _file_list(args: Dict[str, Any]) -> ToolResult:
    from umabot.tools.workspace import get_active_workspace

    ws = get_active_workspace()
    if not ws:
        return ToolResult(content="No active workspace. Set a workspace before listing files.")

    path_str = (args.get("path") or "").strip() or "."

    try:
        ws_root = Path(ws.path).expanduser().resolve()
        candidate = Path(path_str).expanduser()
        if not candidate.is_absolute():
            candidate = ws_root / candidate
        resolved = candidate.resolve()
        resolved.relative_to(ws_root)
    except ValueError:
        return ToolResult(content=f"Path '{path_str}' is outside workspace '{ws.name}'.")

    if not ws.acl.read:
        return ToolResult(content=f"Workspace '{ws.name}' does not allow reads (acl.read=false).")
    if not resolved.exists():
        return ToolResult(content=f"Path not found: {resolved}")
    if resolved.is_file():
        return ToolResult(content=f"'{resolved}' is a file. Use file.read to read it.")

    try:
        entries = sorted(resolved.iterdir(), key=lambda e: (e.is_file(), e.name))
        lines = []
        for entry in entries:
            kind = "f" if entry.is_file() else "d"
            size = str(entry.stat().st_size) if entry.is_file() else "-"
            lines.append(f"{kind}  {entry.name:<40}  {size}")
        header = f"Contents of {resolved}  (workspace: {ws.name})\n"
        return ToolResult(content=header + ("\n".join(lines) if lines else "(empty)"))
    except Exception as exc:
        return ToolResult(content=f"List failed: {exc}")


async def _file_delete(args: Dict[str, Any]) -> ToolResult:
    from umabot.tools.workspace import get_active_workspace

    ws = get_active_workspace()
    if not ws:
        return ToolResult(content="No active workspace.")

    path_str = args.get("path", "").strip()
    if not path_str:
        return ToolResult(content="path is required.")

    try:
        ws_root = Path(ws.path).expanduser().resolve()
        candidate = Path(path_str).expanduser()
        if not candidate.is_absolute():
            candidate = ws_root / candidate
        resolved = candidate.resolve()
        resolved.relative_to(ws_root)
    except ValueError:
        return ToolResult(content=f"Path '{path_str}' is outside workspace '{ws.name}'.")

    if not ws.acl.delete_files:
        return ToolResult(
            content=f"Workspace '{ws.name}' does not allow deleting files (acl.delete_files=false)."
        )
    if not resolved.exists():
        return ToolResult(content=f"File not found: {resolved}")
    if resolved.is_dir():
        return ToolResult(content="Use shell.run with 'rm -rf' to remove directories.")
    try:
        resolved.unlink()
        return ToolResult(content=f"Deleted {resolved}")
    except Exception as exc:
        return ToolResult(content=f"Delete failed: {exc}")
