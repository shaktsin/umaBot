"""Workspace registry — sandboxed rw paths with per-directory ACL enforcement.

Usage
-----
Set the active workspace at the start of each agent/job:

    from umabot.tools.workspace import set_active_workspace, resolve_workspace
    ws = resolve_workspace(name, cfg.tools.workspaces)
    set_active_workspace(ws)

All file tools (file.read, file.write, file.list, file.delete) and shell.run
read the active workspace via get_active_workspace() and enforce the path
boundary + ACL before doing anything.

ContextVar semantics mean each asyncio Task (worker job, spawned agent) gets
its own copy — concurrent jobs don't interfere.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from typing import List, Optional

from umabot.config.schema import WorkspaceConfig

# Per-job active workspace.  Worker sets this before executing tools.
_active_workspace: ContextVar[Optional[WorkspaceConfig]] = ContextVar(
    "active_workspace", default=None
)

# Fallback when no workspace is configured: ~/.umabot/tmp
_TMP_WORKSPACE = WorkspaceConfig(
    name="tmp",
    path=str(Path.home() / ".umabot" / "tmp"),
    default=True,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def set_active_workspace(ws: Optional[WorkspaceConfig]) -> None:
    """Set (or clear) the workspace for the current job/agent."""
    _active_workspace.set(ws)


def get_active_workspace() -> Optional[WorkspaceConfig]:
    """Return the workspace currently active for this asyncio task."""
    return _active_workspace.get()


def resolve_workspace(
    name: str,
    workspaces: List[WorkspaceConfig],
) -> WorkspaceConfig:
    """Return the named workspace, or the default, or the built-in tmp fallback.

    Args:
        name:  Workspace name requested by user/orchestrator (may be empty).
        workspaces: List from cfg.tools.workspaces.
    """
    if name:
        for ws in workspaces:
            if ws.name == name:
                return ws
    for ws in workspaces:
        if ws.default:
            return ws
    if workspaces:
        return workspaces[0]
    return _TMP_WORKSPACE


def detect_workspace_from_text(
    text: str,
    workspaces: List[WorkspaceConfig],
) -> Optional[WorkspaceConfig]:
    """Scan user message for a workspace name mention and return it.

    Looks for patterns like "in my <name> workspace", "use <name>", or just
    the bare workspace name as a word.  Returns None if no match found.
    """
    if not workspaces:
        return None
    lower = text.lower()
    for ws in workspaces:
        if ws.name and ws.name.lower() in lower:
            return ws
    return None


# ---------------------------------------------------------------------------
# Path enforcement
# ---------------------------------------------------------------------------

def enforce_path(
    path_str: str,
    ws: WorkspaceConfig,
    *,
    operation: str,
) -> Path:
    """Resolve *path_str* and verify it's inside *ws* with the ACL allowing *operation*.

    Relative paths are interpreted relative to the workspace root.

    Args:
        path_str:  The path requested by the tool call.
        ws:        Active workspace.
        operation: One of: ``"read"`` | ``"write"`` | ``"create"`` | ``"delete"`` |
                   ``"list"`` | ``"shell"``.

    Returns:
        Absolute resolved Path inside the workspace.

    Raises:
        PermissionError: If the path escapes the workspace or the ACL denies it.
    """
    ws_root = Path(ws.path).expanduser().resolve()
    # Ensure workspace root exists for write/create operations
    if operation in ("write", "create", "shell"):
        ws_root.mkdir(parents=True, exist_ok=True)

    candidate = Path(path_str).expanduser()
    if not candidate.is_absolute():
        candidate = ws_root / candidate
    resolved = candidate.resolve()

    # Containment check
    try:
        resolved.relative_to(ws_root)
    except ValueError:
        raise PermissionError(
            f"Path '{resolved}' is outside workspace '{ws.name}' ({ws_root})"
        )

    # ACL check
    _check_acl(ws, operation)
    return resolved


def _check_acl(ws: WorkspaceConfig, operation: str) -> None:
    acl = ws.acl
    if operation in ("read", "list") and not acl.read:
        raise PermissionError(f"Workspace '{ws.name}' does not allow reads")
    if operation == "write" and not acl.write:
        raise PermissionError(f"Workspace '{ws.name}' does not allow writes")
    if operation == "create" and not acl.create_files:
        raise PermissionError(f"Workspace '{ws.name}' does not allow creating files")
    if operation == "delete" and not acl.delete_files:
        raise PermissionError(f"Workspace '{ws.name}' does not allow deleting files")
    if operation == "shell" and not acl.shell:
        raise PermissionError(f"Workspace '{ws.name}' does not allow shell commands")


def workspace_summary(workspaces: List[WorkspaceConfig]) -> str:
    """Return a human-readable catalog of configured workspaces for prompts."""
    if not workspaces:
        return "  tmp  (~/.umabot/tmp)  [default, rw, shell]"
    lines = []
    for ws in workspaces:
        acl = ws.acl
        flags = []
        if acl.read:
            flags.append("read")
        if acl.write or acl.create_files:
            flags.append("write")
        if acl.delete_files:
            flags.append("delete")
        if acl.shell:
            flags.append("shell")
        default_tag = "  [default]" if ws.default else ""
        lines.append(f"  {ws.name}  ({ws.path})  [{', '.join(flags)}]{default_tag}")
    return "\n".join(lines)
