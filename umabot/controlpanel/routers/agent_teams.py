"""Admin APIs for file-backed agent teams and multi-agent run history."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from umabot.agents.team_registry import TeamRegistry
from umabot.controlpanel.deps import get_config, get_db, get_skill_registry, get_tool_registry
from umabot.storage.db import Database

router = APIRouter(prefix="/admin", tags=["agent-teams"])


class RouteMatcherIn(BaseModel):
    route_type: str = "keyword"
    pattern_or_hint: str
    weight: float = 1.0


class TeamMemberIn(BaseModel):
    role: str
    objective_template: str = ""
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    model: str = ""
    tool_allowlist: List[str] = Field(default_factory=list)
    skill_allowlist: List[str] = Field(default_factory=list)
    workspace: str = ""
    order_index: int = 0
    max_tool_calls: int = 0
    max_iterations: int = 0


class AgentTeamIn(BaseModel):
    id: str = ""
    name: str
    description: str = ""
    enabled: bool = True
    priority: int = 0
    team_type: str = "orchestrator_worker"
    confidence_threshold: float = 0.62
    fit_policy: Dict[str, Any] = Field(default_factory=dict)
    budget_policy: Dict[str, Any] = Field(default_factory=dict)
    retry_policy: Dict[str, Any] = Field(
        default_factory=lambda: {
            "max_retries": 2,
            "fail_on_defer": True,
            "require_blockers_section": True,
            "enforce_shell_success": True,
        }
    )
    tool_pool: List[str] = Field(default_factory=list)
    required_capabilities: List[str] = Field(default_factory=list)
    capability_overrides: Dict[str, List[str]] = Field(default_factory=dict)
    rules_markdown: str = ""
    worksteps_markdown: str = ""
    members: List[TeamMemberIn]
    routes: List[RouteMatcherIn]


class BuildFromPromptIn(BaseModel):
    prompt: str


class TestRouteIn(BaseModel):
    task: str


class InstallTeamIn(BaseModel):
    source: str
    name: Optional[str] = None


class SkillIn(BaseModel):
    skill_key: str
    name: str
    description: str = ""
    version: str = "1.0.0"
    required_tools: List[str] = Field(default_factory=list)
    prompt_template: str = ""
    enabled: bool = True


def _team_registry(db: Database, config, tool_registry, skill_registry) -> TeamRegistry:
    return TeamRegistry(
        db=db,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        config=config,
    )


@router.get("/agent-teams/sources")
async def get_agent_team_sources(
    db: Database = Depends(get_db),
    config=Depends(get_config),
    tool_registry=Depends(get_tool_registry),
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    registry = _team_registry(db, config, tool_registry, skill_registry)
    return registry.list_sources()


@router.post("/agent-teams/install")
async def install_agent_team(
    body: InstallTeamIn,
    db: Database = Depends(get_db),
    config=Depends(get_config),
    tool_registry=Depends(get_tool_registry),
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    registry = _team_registry(db, config, tool_registry, skill_registry)
    try:
        return registry.install_team(body.source, name=body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/agent-teams/install/{team_id}")
async def uninstall_agent_team(
    team_id: str,
    db: Database = Depends(get_db),
    config=Depends(get_config),
    tool_registry=Depends(get_tool_registry),
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    registry = _team_registry(db, config, tool_registry, skill_registry)
    try:
        ok = registry.uninstall_team(team_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"Team not found: {team_id}")
    return {"status": "removed", "id": team_id}


@router.get("/agent-teams")
async def list_agent_teams(
    enabled_only: bool = False,
    db: Database = Depends(get_db),
    config=Depends(get_config),
    tool_registry=Depends(get_tool_registry),
    skill_registry=Depends(get_skill_registry),
) -> List[Dict[str, Any]]:
    registry = _team_registry(db, config, tool_registry, skill_registry)
    return registry.list_teams(enabled_only=enabled_only)


@router.post("/agent-teams")
async def create_agent_team(
    body: AgentTeamIn,
    db: Database = Depends(get_db),
    config=Depends(get_config),
    tool_registry=Depends(get_tool_registry),
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    registry = _team_registry(db, config, tool_registry, skill_registry)
    try:
        return registry.create_team(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/agent-teams/team/{team_id}")
async def get_agent_team(
    team_id: str,
    db: Database = Depends(get_db),
    config=Depends(get_config),
    tool_registry=Depends(get_tool_registry),
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    registry = _team_registry(db, config, tool_registry, skill_registry)
    team = registry.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"Team not found: {team_id}")
    return team


@router.put("/agent-teams/team/{team_id}")
async def update_agent_team(
    team_id: str,
    body: AgentTeamIn,
    db: Database = Depends(get_db),
    config=Depends(get_config),
    tool_registry=Depends(get_tool_registry),
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    registry = _team_registry(db, config, tool_registry, skill_registry)
    try:
        updated = registry.update_team(team_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail=f"Team not found: {team_id}")
    return updated


@router.delete("/agent-teams/team/{team_id}")
async def delete_agent_team(
    team_id: str,
    db: Database = Depends(get_db),
    config=Depends(get_config),
    tool_registry=Depends(get_tool_registry),
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    registry = _team_registry(db, config, tool_registry, skill_registry)
    try:
        ok = registry.delete_team(team_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"Team not found: {team_id}")
    return {"status": "deleted", "id": team_id}


@router.post("/agent-teams/build-from-prompt")
async def build_team_from_prompt(
    body: BuildFromPromptIn,
    db: Database = Depends(get_db),
    config=Depends(get_config),
    tool_registry=Depends(get_tool_registry),
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    registry = _team_registry(db, config, tool_registry, skill_registry)
    return registry.build_from_prompt(body.prompt)


@router.post("/agent-teams/test-route")
async def test_agent_team_route(
    body: TestRouteIn,
    db: Database = Depends(get_db),
    config=Depends(get_config),
    tool_registry=Depends(get_tool_registry),
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    registry = _team_registry(db, config, tool_registry, skill_registry)
    team_cfg = getattr(config, "agent_teams", None)
    allowed_tools = list(tool_registry.list().keys())
    return registry.test_route(
        task=body.task,
        allowed_tools=allowed_tools,
        default_threshold=float(getattr(team_cfg, "default_confidence_threshold", 0.62) or 0.62),
        max_teams_considered=int(getattr(team_cfg, "max_teams_considered", 20) or 20),
        routing_mode=str(getattr(team_cfg, "routing_mode", "hybrid") or "hybrid"),
    )


@router.post("/agent-teams/team/{team_id}/dry-run")
async def dry_run_agent_team(
    team_id: str,
    body: TestRouteIn,
    db: Database = Depends(get_db),
    config=Depends(get_config),
    tool_registry=Depends(get_tool_registry),
    skill_registry=Depends(get_skill_registry),
) -> Dict[str, Any]:
    registry = _team_registry(db, config, tool_registry, skill_registry)
    team = registry.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"Team not found: {team_id}")

    prepared = registry.test_route(
        task=body.task,
        allowed_tools=list(tool_registry.list().keys()),
        default_threshold=float(team.get("confidence_threshold", 0.62) or 0.62),
        max_teams_considered=1,
        routing_mode="rule",
    )
    return {
        "team": team,
        "task": body.task,
        "route_preview": prepared,
        "estimated_member_count": len(team.get("members", []) or []),
        "dry_run": True,
    }


@router.get("/agent-skills")
async def list_agent_skills(
    enabled_only: bool = False,
    db: Database = Depends(get_db),
    config=Depends(get_config),
    tool_registry=Depends(get_tool_registry),
    skill_registry=Depends(get_skill_registry),
) -> List[Dict[str, Any]]:
    registry = _team_registry(db, config, tool_registry, skill_registry)
    return registry.list_skills(enabled_only=enabled_only)


@router.post("/agent-skills")
async def create_agent_skill(body: SkillIn) -> Dict[str, Any]:
    raise HTTPException(status_code=400, detail="Agent skills are file-managed. Use /api/skills install/remove.")


@router.get("/agent-skills/{skill_id}")
async def get_agent_skill(skill_id: int) -> Dict[str, Any]:
    raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")


@router.put("/agent-skills/{skill_id}")
async def update_agent_skill(skill_id: int, body: SkillIn) -> Dict[str, Any]:
    raise HTTPException(status_code=400, detail="Agent skills are file-managed. Use /api/skills install/remove.")


@router.delete("/agent-skills/{skill_id}")
async def delete_agent_skill(skill_id: int) -> Dict[str, Any]:
    raise HTTPException(status_code=400, detail="Agent skills are file-managed. Use /api/skills install/remove.")


@router.get("/agent-teams/runs")
async def list_agent_team_runs(
    limit: int = Query(50, ge=1, le=500),
    db: Database = Depends(get_db),
) -> List[Dict[str, Any]]:
    return db.list_agent_team_runs(limit=limit)


@router.get("/agent-teams/runs/{run_id}")
async def get_agent_team_run(
    run_id: str,
    db: Database = Depends(get_db),
) -> Dict[str, Any]:
    item = db.get_agent_team_run(run_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    item["events"] = db.list_agent_team_events(run_id)
    item["checkpoints"] = db.list_agent_team_checkpoints(run_id)
    return item


@router.get("/multi-agent/runs")
async def list_multi_agent_runs(
    status: str = "",
    limit: int = Query(50, ge=1, le=500),
    db: Database = Depends(get_db),
) -> List[Dict[str, Any]]:
    return db.list_agent_team_runs(limit=limit, status=status)


@router.get("/multi-agent/runs/{run_id}")
async def get_multi_agent_run(
    run_id: str,
    db: Database = Depends(get_db),
) -> Dict[str, Any]:
    item = db.get_agent_team_run(run_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    item["events"] = db.list_agent_team_events(run_id)
    return item


@router.get("/multi-agent/runs/{run_id}/dag")
async def get_multi_agent_run_dag(
    run_id: str,
    db: Database = Depends(get_db),
) -> Dict[str, Any]:
    events = db.list_agent_team_events(run_id)
    nodes: Dict[str, Dict[str, Any]] = {}
    root_node_id = ""
    task = ""
    status = "running"

    for event in events:
        name = event.get("event_name")
        payload = event.get("payload", {}) or {}
        if name == "multi_agent_run_started":
            root_node_id = str(payload.get("root_node_id", root_node_id))
            task = str(payload.get("task", task))
            status = str(payload.get("status", status))
            for item in payload.get("nodes", []) or []:
                node_id = str(item.get("node_id", "")).strip()
                if node_id:
                    nodes[node_id] = dict(item)
                    nodes[node_id].setdefault("logs", [])
        elif name in {"multi_agent_node_added", "multi_agent_node_status"}:
            node_id = str(payload.get("node_id", "")).strip()
            if not node_id:
                continue
            node = nodes.setdefault(node_id, {"node_id": node_id, "logs": []})
            node.update(payload)
            node.setdefault("logs", [])
        elif name == "multi_agent_node_log":
            node_id = str(payload.get("node_id", "")).strip()
            if not node_id:
                continue
            node = nodes.setdefault(node_id, {"node_id": node_id, "logs": []})
            node.setdefault("logs", []).append(
                {
                    "timestamp": payload.get("timestamp"),
                    "event_type": payload.get("event_type"),
                    "message": payload.get("message"),
                    "payload": payload.get("payload", {}),
                }
            )
        elif name == "multi_agent_run_completed":
            status = str(payload.get("status", status))

    return {
        "run_id": run_id,
        "task": task,
        "status": status,
        "root_node_id": root_node_id,
        "nodes": nodes,
    }


@router.get("/multi-agent/runs/{run_id}/agents")
async def get_multi_agent_run_agents(
    run_id: str,
    db: Database = Depends(get_db),
) -> List[Dict[str, Any]]:
    dag = await get_multi_agent_run_dag(run_id=run_id, db=db)
    return list((dag.get("nodes") or {}).values())


@router.get("/multi-agent/agents/{agent_id}/logs")
async def get_multi_agent_agent_logs(
    agent_id: str,
    run_id: str,
    db: Database = Depends(get_db),
) -> Dict[str, Any]:
    events = db.list_agent_team_events(run_id)
    logs: List[Dict[str, Any]] = []
    for event in events:
        if event.get("event_name") != "multi_agent_node_log":
            continue
        payload = event.get("payload", {}) or {}
        if str(payload.get("node_id", "")) != agent_id:
            continue
        logs.append(
            {
                "timestamp": payload.get("timestamp"),
                "event_type": payload.get("event_type"),
                "message": payload.get("message"),
                "payload": payload.get("payload", {}),
            }
        )
    return {"agent_id": agent_id, "run_id": run_id, "logs": logs}


@router.get("/multi-agent/runs/{run_id}/route")
async def get_multi_agent_run_route(
    run_id: str,
    db: Database = Depends(get_db),
) -> Dict[str, Any]:
    run = db.get_agent_team_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return {
        "run_id": run_id,
        "selected_by": run.get("selected_by"),
        "route_rationale": run.get("route_rationale", {}),
    }


@router.get("/multi-agent/runs/{run_id}/budget")
async def get_multi_agent_run_budget(
    run_id: str,
    db: Database = Depends(get_db),
) -> Dict[str, Any]:
    run = db.get_agent_team_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return {
        "run_id": run_id,
        "budget_snapshot": run.get("budget_snapshot", {}),
        "complexity_class": run.get("complexity_class"),
    }


@router.get("/multi-agent/runs/{run_id}/checkpoints")
async def get_multi_agent_run_checkpoints(
    run_id: str,
    db: Database = Depends(get_db),
) -> Dict[str, Any]:
    return {"run_id": run_id, "checkpoints": db.list_agent_team_checkpoints(run_id)}


@router.get("/multi-agent/runs/{run_id}/retries")
async def get_multi_agent_run_retries(
    run_id: str,
    db: Database = Depends(get_db),
) -> Dict[str, Any]:
    events = db.list_agent_team_events(run_id)
    retries = [e for e in events if str(e.get("event_name", "")).endswith("retry")]
    return {"run_id": run_id, "retries": retries}


@router.get("/multi-agent/runs/{run_id}/skills")
async def get_multi_agent_run_skills(
    run_id: str,
    db: Database = Depends(get_db),
) -> Dict[str, Any]:
    events = db.list_agent_team_events(run_id)
    usage: List[Dict[str, Any]] = []
    for event in events:
        if event.get("event_name") not in {
            "team.member.started",
            "team.skill.selected",
            "team.skill.used",
            "team.skill.denied",
        }:
            continue
        usage.append(event)
    return {"run_id": run_id, "skills": usage}
