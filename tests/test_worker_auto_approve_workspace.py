from __future__ import annotations

import asyncio
from types import SimpleNamespace

from umabot.config.schema import WorkspaceACL, WorkspaceConfig
from umabot.policy.rules import DeclarativePolicyEngine
from umabot.tools import ToolRegistry
from umabot.tools.builtin import register_builtin_tools
from umabot.tools.workspace import set_active_workspace
from umabot.worker import Worker


def _worker_with_policy(*, workspaces: list[str], tools: list[str], shell_cmds: list[str]) -> Worker:
    worker = Worker.__new__(Worker)
    worker.config = SimpleNamespace(
        policy=SimpleNamespace(
            approval_mode="auto_approve_workspace",
            auto_approve_workspaces=workspaces,
            auto_approve_tools=tools,
            auto_approve_shell_commands=shell_cmds,
        )
    )
    worker.tool_registry = ToolRegistry()
    register_builtin_tools(worker.tool_registry, enable_shell=True)
    worker.declarative_policy = DeclarativePolicyEngine([])
    worker.security_policy = None
    worker.db = SimpleNamespace(add_audit=lambda *args, **kwargs: None)
    return worker


def test_auto_approve_workspace_allows_file_write_inside_allowed_workspace(tmp_path) -> None:
    ws = WorkspaceConfig(
        name="projects",
        path=str(tmp_path),
        acl=WorkspaceACL(read=True, write=True, create_files=True, delete_files=True, shell=True),
        default=True,
    )
    set_active_workspace(ws)
    try:
        worker = _worker_with_policy(
            workspaces=["projects"],
            tools=["file.*"],
            shell_cmds=[],
        )
        ok = worker._should_auto_approve_tool_confirmation(
            tool_name="file.write",
            tool_arguments={"path": "app/main.py", "content": "print('ok')"},
            allowed_tools=["file.write", "file.read", "shell.run"],
        )
        assert ok is True
    finally:
        set_active_workspace(None)


def test_auto_approve_workspace_rejects_workspace_not_in_allowlist(tmp_path) -> None:
    ws = WorkspaceConfig(
        name="downloads",
        path=str(tmp_path),
        acl=WorkspaceACL(read=True, write=True, create_files=True, delete_files=True, shell=True),
        default=True,
    )
    set_active_workspace(ws)
    try:
        worker = _worker_with_policy(
            workspaces=["projects"],
            tools=["file.*"],
            shell_cmds=[],
        )
        ok = worker._should_auto_approve_tool_confirmation(
            tool_name="file.write",
            tool_arguments={"path": "note.txt", "content": "x"},
            allowed_tools=["file.write"],
        )
        assert ok is False
    finally:
        set_active_workspace(None)


def test_auto_approve_workspace_shell_requires_safe_prefix_and_no_chaining(tmp_path) -> None:
    ws = WorkspaceConfig(
        name="projects",
        path=str(tmp_path),
        acl=WorkspaceACL(read=True, write=True, create_files=True, delete_files=True, shell=True),
        default=True,
    )
    set_active_workspace(ws)
    try:
        worker = _worker_with_policy(
            workspaces=["projects"],
            tools=["shell.run"],
            shell_cmds=["npm run", "pytest"],
        )
        allowed = worker._should_auto_approve_tool_confirmation(
            tool_name="shell.run",
            tool_arguments={"cmd": "npm run build"},
            allowed_tools=["shell.run"],
        )
        blocked = worker._should_auto_approve_tool_confirmation(
            tool_name="shell.run",
            tool_arguments={"cmd": "npm run build && cat /etc/passwd"},
            allowed_tools=["shell.run"],
        )
        assert allowed is True
        assert blocked is False
    finally:
        set_active_workspace(None)


def test_agent_tool_guard_enforces_auto_approve_for_red_tools(tmp_path) -> None:
    ws = WorkspaceConfig(
        name="projects",
        path=str(tmp_path),
        acl=WorkspaceACL(read=True, write=True, create_files=True, delete_files=True, shell=True),
        default=True,
    )
    set_active_workspace(ws)
    try:
        worker = _worker_with_policy(
            workspaces=["projects"],
            tools=["shell.run"],
            shell_cmds=["pytest", "npm run"],
        )
        guard = worker._build_agent_tool_guard(
            allowed_tools=["shell.run", "file.write"],
            chat_id="admin",
            channel="web",
            connector="web-panel",
            source_connector="web-panel",
            connector_role="admin",
            kind="external",
            action="",
            importance="",
            needs_admin=True,
            admin_explicit=True,
        )

        # Allowed prefix => auto-approved.
        asyncio.run(guard("shell.run", {"cmd": "pytest -q"}))

        blocked = None
        try:
            asyncio.run(guard("shell.run", {"cmd": "curl https://example.com"}))
        except Exception as exc:  # noqa: BLE001
            blocked = str(exc)
        assert blocked is not None
        assert "auto_approve_workspace" in blocked
    finally:
        set_active_workspace(None)
