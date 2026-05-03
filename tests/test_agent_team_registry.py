from __future__ import annotations

from pathlib import Path

from umabot.agents.team_registry import TeamRegistry
from umabot.skills.registry import SkillRegistry
from umabot.storage.db import Database
from umabot.tools.registry import Tool, ToolRegistry, ToolResult


async def _noop_tool(_args):
    return ToolResult(content="ok")


def _build_tool_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="shell.run",
            schema={"type": "object", "properties": {}, "additionalProperties": True},
            handler=_noop_tool,
            description="Run shell command",
        )
    )
    reg.register(
        Tool(
            name="web.search",
            schema={"type": "object", "properties": {}, "additionalProperties": True},
            handler=_noop_tool,
            description="Search web",
        )
    )
    reg.register(
        Tool(
            name="mcp_playwright_navigate",
            schema={"type": "object", "properties": {}, "additionalProperties": True},
            handler=_noop_tool,
            description="Navigate browser",
        )
    )
    reg.register(
        Tool(
            name="mcp_playwright_screenshot",
            schema={"type": "object", "properties": {}, "additionalProperties": True},
            handler=_noop_tool,
            description="Capture screenshot",
        )
    )
    return reg


def _write_skill(skill_root: Path) -> None:
    skill_dir = skill_root / "ops-shell"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: ops-shell
description: Operational shell tasks
metadata:
  required_tools:
    - shell.run
---
Use shell tools for operational tasks.
""",
        encoding="utf-8",
    )


def _write_team(team_root: Path) -> None:
    team_dir = team_root / "infra-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "TEAM.md").write_text(
        """---
id: infra-team
name: Infra Team
description: Handles deployment and infrastructure diagnostics
enabled: true
priority: 5
team_type: chain
confidence_threshold: 0.4
routes:
  - route_type: keyword
    pattern_or_hint: deployment
    weight: 1.0
  - route_type: keyword
    pattern_or_hint: infra
    weight: 1.0
members:
  - role: Ops Engineer
    objective_template: Diagnose deployment issue
    tool_allowlist:
      - shell.run
      - web.search
    skill_allowlist:
      - ops-shell
    workspace: ""
---
""",
        encoding="utf-8",
    )


def test_team_registry_selects_matching_team_with_skill_enforced_tools(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "umabot.db"))

    skills_root = tmp_path / "skills"
    _write_skill(skills_root)
    skill_registry = SkillRegistry()
    skill_registry.load_from_dirs([skills_root])

    teams_root = tmp_path / "agent_teams"
    _write_team(teams_root)

    registry = TeamRegistry(
        db=db,
        tool_registry=_build_tool_registry(),
        skill_registry=skill_registry,
        team_dirs=[teams_root],
        install_dir=tmp_path / "installed-teams",
    )

    selection = registry.select_team(
        task="Please investigate this deployment failure in infra",
        allowed_tools=["shell.run", "web.search"],
        default_threshold=0.62,
        max_teams_considered=20,
        routing_mode="hybrid",
    )

    assert selection.team is not None
    assert selection.team["id"] == "infra-team"
    assert selection.score >= selection.threshold
    assert selection.team["members"][0]["effective_tools"] == ["shell.run"]


def test_fit_check_rejects_simple_short_tasks(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "umabot.db"))
    registry = TeamRegistry(
        db=db,
        tool_registry=_build_tool_registry(),
        skill_registry=SkillRegistry(),
        team_dirs=[tmp_path / "installed-teams"],
        install_dir=tmp_path / "installed-teams",
    )

    fit = registry.fit_check("hi", min_len=20, enabled=True)

    assert fit["passed"] is False
    assert "too short" in fit["reason"]


def test_team_runtime_capability_wildcards_resolve_to_tool_pool(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "umabot.db"))
    registry = TeamRegistry(
        db=db,
        tool_registry=_build_tool_registry(),
        skill_registry=SkillRegistry(),
        team_dirs=[tmp_path / "installed-teams"],
        install_dir=tmp_path / "installed-teams",
    )

    team = registry.create_team(
        {
            "id": "browser-proof",
            "name": "Browser Proof Team",
            "description": "Needs browser capture",
            "enabled": True,
            "team_type": "orchestrator_worker",
            "members": [
                {
                    "role": "Verifier",
                    "objective_template": "Capture screenshots",
                    "tool_allowlist": [],
                    "skill_allowlist": [],
                }
            ],
            "routes": [
                {"route_type": "keyword", "pattern_or_hint": "screenshot", "weight": 1.0}
            ],
            "tool_pool": ["mcp_playwright_navigate", "mcp_playwright_screenshot", "shell.run"],
            "required_capabilities": ["screenshot_*", "browser_navigation"],
        }
    )

    prepared = registry.test_route(
        task="take screenshot and browse",
        allowed_tools=list(_build_tool_registry().list().keys()),
        default_threshold=0.1,
        max_teams_considered=5,
        routing_mode="rule",
    )["selected"]

    assert team["id"] == "browser-proof"
    assert prepared is not None
    assert prepared["capability_preflight"]["ok"] is True
    assert "mcp_playwright_screenshot" in prepared["runtime_tool_pool"]
    assert "mcp_playwright_navigate" in prepared["runtime_tool_pool"]


def test_team_runtime_capability_preflight_reports_missing_patterns(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "umabot.db"))
    registry = TeamRegistry(
        db=db,
        tool_registry=_build_tool_registry(),
        skill_registry=SkillRegistry(),
        team_dirs=[tmp_path / "installed-teams"],
        install_dir=tmp_path / "installed-teams",
    )

    registry.create_team(
        {
            "id": "needs-missing-cap",
            "name": "Missing Cap Team",
            "description": "Requires unknown capability",
            "enabled": True,
            "team_type": "orchestrator_worker",
            "members": [
                {
                    "role": "Executor",
                    "objective_template": "Do work",
                    "tool_allowlist": [],
                    "skill_allowlist": [],
                }
            ],
            "routes": [
                {"route_type": "keyword", "pattern_or_hint": "unknown", "weight": 1.0}
            ],
            "required_capabilities": ["nonexistent_*"],
        }
    )

    prepared = registry.test_route(
        task="unknown task",
        allowed_tools=list(_build_tool_registry().list().keys()),
        default_threshold=0.1,
        max_teams_considered=5,
        routing_mode="rule",
    )["selected"]

    assert prepared is not None
    assert prepared["capability_preflight"]["ok"] is False
    assert "nonexistent_*" in prepared["capability_preflight"]["missing_patterns"]
