from __future__ import annotations

import logging
import fnmatch
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import yaml


logger = logging.getLogger("umabot.agents.team_registry")

_COMPLEXITY_KEYWORDS = {
    "complex": {
        "research",
        "investigate",
        "deep",
        "comprehensive",
        "end-to-end",
        "architecture",
        "multi-step",
        "workflow",
        "orchestrate",
        "pipeline",
    },
    "moderate": {
        "compare",
        "evaluate",
        "analyze",
        "analyse",
        "plan",
        "design",
        "implement",
        "refactor",
        "debug",
        "summarize",
    },
}

_TEAM_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")

# Generic capability map; values are tool-name glob patterns.
# Teams can reference these capability keys directly or via wildcard patterns
# in required_capabilities (e.g. "browser_*", "screenshot_*", "*").
_CAPABILITY_TOOL_PATTERNS: Dict[str, List[str]] = {
    "screenshot_capture": [
        "mcp_*screenshot*",
        "mcp_playwright_*",
    ],
    "browser_navigation": [
        "mcp_*navigate*",
        "mcp_*goto*",
        "mcp_playwright_*",
    ],
    "browser_interaction": [
        "mcp_*click*",
        "mcp_*type*",
        "mcp_playwright_*",
    ],
    "http_probe": [
        "shell.run",
        "http.*",
        "web.*",
        "mcp_*fetch*",
        "mcp_*request*",
    ],
    "build_and_test": [
        "shell.run",
    ],
    "filesystem_rw": [
        "file.read",
        "file.list",
        "file.write",
        "file.delete",
    ],
}

_DEFAULT_RETRY_POLICY_BY_TYPE: Dict[str, Dict[str, Any]] = {
    "orchestrator_worker": {
        "max_retries": 2,
        "fail_on_defer": True,
        "require_blockers_section": True,
        "enforce_shell_success": True,
    },
    "hybrid": {
        "max_retries": 2,
        "fail_on_defer": True,
        "require_blockers_section": True,
        "enforce_shell_success": True,
    },
    "chain": {
        "max_retries": 0,
        "fail_on_defer": False,
        "require_blockers_section": False,
        "enforce_shell_success": False,
    },
    "parallel": {
        "max_retries": 0,
        "fail_on_defer": False,
        "require_blockers_section": False,
        "enforce_shell_success": False,
    },
}


@dataclass
class TeamSelection:
    team: Optional[Dict[str, Any]]
    score: float
    threshold: float
    selected_by: str
    rationale: Dict[str, Any]


class TeamRegistry:
    """File-backed team registry + routing/fit for reusable agent teams.

    Team definition format (per directory):
      - TEAM.md (YAML frontmatter)
      - RULES.md (optional)
      - WORKSTEPS.md (optional)
    """

    def __init__(
        self,
        *,
        db,
        tool_registry,
        skill_registry,
        config=None,
        team_dirs: Optional[List[Path]] = None,
        install_dir: Optional[Path] = None,
    ) -> None:
        self.db = db
        self.tool_registry = tool_registry
        self.skill_registry = skill_registry
        self.config = config

        self._team_dirs = [Path(p).expanduser() for p in (team_dirs or _default_team_dirs(config))]
        self._install_dir = Path(install_dir).expanduser() if install_dir else Path(_default_install_dir(config)).expanduser()
        self._install_dir.mkdir(parents=True, exist_ok=True)

        self._teams: Dict[str, Dict[str, Any]] = {}
        self.refresh()

    # ------------------------------------------------------------------
    # Discovery and management
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        teams: Dict[str, Dict[str, Any]] = {}
        for root in self._team_dirs:
            if not root.exists() or not root.is_dir():
                continue
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                team = self._load_team_dir(child)
                if not team:
                    continue
                key = str(team["id"])
                if key in teams:
                    logger.warning("Duplicate team id '%s' from %s ignored", key, child)
                    continue
                teams[key] = team
        self._teams = teams

    def list_sources(self) -> Dict[str, Any]:
        return {
            "team_dirs": [str(p) for p in self._team_dirs],
            "install_dir": str(self._install_dir),
        }

    def list_teams(self, *, enabled_only: bool = False) -> List[Dict[str, Any]]:
        self.refresh()
        items = list(self._teams.values())
        if enabled_only:
            items = [item for item in items if bool(item.get("enabled", True))]
        items.sort(key=lambda t: (-int(t.get("priority", 0)), str(t.get("name", "")).lower()))
        return [self._normalize_team(item) for item in items]

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        self.refresh()
        item = self._teams.get(str(team_id).strip())
        if not item:
            return None
        return self._normalize_team(item)

    def create_team(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        team = self._normalize_team(payload)
        if not team["id"]:
            team["id"] = _slugify(team.get("name", ""))
        self._validate_team(team)
        team_dir = self._install_dir / str(team["id"])
        if team_dir.exists():
            raise ValueError(f"team already exists: {team['id']}")
        self._write_team_dir(team_dir, team)
        self.refresh()
        created = self.get_team(str(team["id"]))
        assert created is not None
        return created

    def update_team(self, team_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        existing = self.get_team(team_id)
        if not existing:
            return None
        source_dir = Path(str(existing.get("source_dir", "")))
        if not self._is_writable_team_dir(source_dir):
            raise ValueError("team is not writable (not in install dir)")

        merged = dict(existing)
        merged.update(payload or {})
        merged["id"] = str(team_id)
        if "members" in payload:
            merged["members"] = payload.get("members") or []
        if "routes" in payload:
            merged["routes"] = payload.get("routes") or []

        team = self._normalize_team(merged)
        team["id"] = str(team_id)
        self._validate_team(team)
        self._write_team_dir(source_dir, team)
        self.refresh()
        return self.get_team(str(team_id))

    def delete_team(self, team_id: str) -> bool:
        existing = self.get_team(team_id)
        if not existing:
            return False
        source_dir = Path(str(existing.get("source_dir", "")))
        if not self._is_writable_team_dir(source_dir):
            raise ValueError("team is not writable (not in install dir)")
        shutil.rmtree(source_dir)
        self.refresh()
        return True

    def install_team(self, source: str, *, name: Optional[str] = None) -> Dict[str, Any]:
        if source.startswith(("http://", "https://", "git@")):
            ok, msg, team_name = self._install_from_git(source=source, name=name)
        else:
            ok, msg, team_name = self._install_from_path(Path(source), name=name)

        if not ok:
            raise ValueError(msg)

        self.refresh()
        installed = self.get_team(team_name)
        if not installed:
            raise ValueError(f"Installed team not found: {team_name}")
        return {
            "status": "installed",
            "message": msg,
            "team": installed,
        }

    def uninstall_team(self, team_id: str) -> bool:
        return self.delete_team(team_id)

    # ------------------------------------------------------------------
    # Skills view (read-only from loaded skills)
    # ------------------------------------------------------------------

    def list_skills(self, *, enabled_only: bool = False) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        loaded = sorted(self.skill_registry.list(), key=lambda s: s.metadata.name)
        for idx, skill in enumerate(loaded, start=1):
            required_tools = self._required_tools_from_skill(skill.metadata.name)
            items.append(
                {
                    "id": idx,
                    "skill_key": skill.metadata.name,
                    "name": skill.metadata.name,
                    "description": skill.metadata.description,
                    "version": str((skill.metadata.metadata or {}).get("version", "1.0.0")),
                    "required_tools": required_tools,
                    "prompt_template": skill.metadata.body,
                    "enabled": True,
                    "created_at": "",
                    "updated_at": "",
                }
            )
        return items

    def get_skill(self, _skill_id: int) -> Optional[Dict[str, Any]]:
        return None

    def upsert_skill(self, _payload: Dict[str, Any], *, skill_id: Optional[int] = None) -> Dict[str, Any]:
        raise ValueError("Agent skills are file-managed. Use /api/skills install/remove.")

    def delete_skill(self, _skill_id: int) -> bool:
        raise ValueError("Agent skills are file-managed. Use /api/skills install/remove.")

    # ------------------------------------------------------------------
    # Fit and routing
    # ------------------------------------------------------------------

    def fit_check(self, task: str, *, min_len: int = 60, enabled: bool = True) -> Dict[str, Any]:
        text = (task or "").strip()
        complexity = self.classify_complexity(text)

        if not enabled:
            return {
                "passed": True,
                "reason": "fit gate disabled",
                "complexity_class": complexity,
            }

        if len(text) < max(1, min_len):
            return {
                "passed": False,
                "reason": f"task too short ({len(text)} chars < {min_len})",
                "complexity_class": complexity,
            }

        if complexity == "simple":
            return {
                "passed": False,
                "reason": "task classified as simple",
                "complexity_class": complexity,
            }

        return {
            "passed": True,
            "reason": f"task classified as {complexity}",
            "complexity_class": complexity,
        }

    def select_team(
        self,
        *,
        task: str,
        allowed_tools: List[str],
        default_threshold: float,
        max_teams_considered: int,
        routing_mode: str = "hybrid",
    ) -> TeamSelection:
        teams = self.list_teams(enabled_only=True)
        teams = teams[: max(1, int(max_teams_considered or 20))]

        if not teams:
            return TeamSelection(
                team=None,
                score=0.0,
                threshold=float(default_threshold),
                selected_by="rule",
                rationale={"reason": "no enabled teams"},
            )

        scored: List[Tuple[Dict[str, Any], float, Dict[str, Any]]] = []
        for team in teams:
            score, breakdown = self._score_team(team, task)
            scored.append((team, score, breakdown))

        scored.sort(
            key=lambda item: (item[1], int(item[0].get("priority", 0)), str(item[0].get("id", ""))),
            reverse=True,
        )
        best_team, best_score, breakdown = scored[0]
        threshold = float(best_team.get("confidence_threshold", default_threshold) or default_threshold)

        if best_score < threshold:
            return TeamSelection(
                team=None,
                score=best_score,
                threshold=threshold,
                selected_by=self._normalize_selected_by(routing_mode),
                rationale={
                    "reason": "best score below threshold",
                    "best_team_id": best_team.get("id"),
                    "breakdown": breakdown,
                    "top_candidates": [
                        {
                            "team_id": t.get("id"),
                            "name": t.get("name"),
                            "score": s,
                        }
                        for t, s, _ in scored[:3]
                    ],
                },
            )

        prepared = self._prepare_team_for_runtime(best_team, allowed_tools)
        return TeamSelection(
            team=prepared,
            score=best_score,
            threshold=threshold,
            selected_by=self._normalize_selected_by(routing_mode),
            rationale={
                "breakdown": breakdown,
                "top_candidates": [
                    {
                        "team_id": t.get("id"),
                        "name": t.get("name"),
                        "score": s,
                    }
                    for t, s, _ in scored[:3]
                ],
            },
        )

    def test_route(
        self,
        *,
        task: str,
        allowed_tools: Optional[List[str]] = None,
        default_threshold: float = 0.62,
        max_teams_considered: int = 20,
        routing_mode: str = "hybrid",
    ) -> Dict[str, Any]:
        selection = self.select_team(
            task=task,
            allowed_tools=list(allowed_tools or []),
            default_threshold=default_threshold,
            max_teams_considered=max_teams_considered,
            routing_mode=routing_mode,
        )
        return {
            "selected": selection.team,
            "score": selection.score,
            "threshold": selection.threshold,
            "selected_by": selection.selected_by,
            "rationale": selection.rationale,
        }

    # ------------------------------------------------------------------
    # Draft builder
    # ------------------------------------------------------------------

    def build_from_prompt(self, prompt: str) -> Dict[str, Any]:
        text = (prompt or "").strip()
        lowered = text.lower()

        if any(k in lowered for k in ("parallel", "in parallel", "concurrently")):
            team_type = "parallel"
        elif any(k in lowered for k in ("chain", "step by step", "pipeline")):
            team_type = "chain"
        else:
            team_type = "orchestrator_worker"

        name = _slug_to_title(text.split(".")[0][:80]) or "Generated Team"
        team_key = _slugify(name)
        keyword_tokens = _extract_keywords(lowered)

        skill_keys = [item["skill_key"] for item in self.list_skills(enabled_only=True)]

        members = [
            {
                "role": "Planner",
                "objective_template": "Break down the task and define execution plan.",
                "output_schema": {"type": "object", "properties": {"plan": {"type": "array"}}},
                "model": "",
                "tool_allowlist": [],
                "skill_allowlist": skill_keys[:1],
                "workspace": "",
                "order_index": 0,
                "max_tool_calls": 10,
                "max_iterations": 8,
            },
            {
                "role": "Executor",
                "objective_template": "Execute the plan and produce complete output.",
                "output_schema": {"type": "object", "properties": {"result": {"type": "string"}}},
                "model": "",
                "tool_allowlist": [],
                "skill_allowlist": skill_keys[:2],
                "workspace": "",
                "order_index": 1,
                "max_tool_calls": 15,
                "max_iterations": 12,
            },
        ]

        routes = [
            {
                "route_type": "keyword",
                "pattern_or_hint": token,
                "weight": 1.0,
            }
            for token in keyword_tokens[:8]
        ]
        if not routes:
            routes = [
                {
                    "route_type": "keyword",
                    "pattern_or_hint": "default",
                    "weight": 0.6,
                }
            ]

        return self._normalize_team(
            {
                "id": team_key,
                "name": name,
                "description": text,
                "enabled": False,
                "priority": 0,
                "team_type": team_type,
                "confidence_threshold": 0.62,
                "fit_policy": {},
                "budget_policy": {},
                "retry_policy": dict(_DEFAULT_RETRY_POLICY_BY_TYPE.get(team_type, {})),
                "tool_pool": [],
                "required_capabilities": [],
                "capability_overrides": {},
                "members": members,
                "routes": routes,
                "rules_markdown": "# Rules\n\nDefine hard constraints for this team.",
                "worksteps_markdown": "# Worksteps\n\n1. Plan\n2. Execute\n3. Verify",
            }
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def classify_complexity(self, task: str) -> str:
        lowered = (task or "").lower()
        score_complex = sum(1 for t in _COMPLEXITY_KEYWORDS["complex"] if t in lowered)
        score_moderate = sum(1 for t in _COMPLEXITY_KEYWORDS["moderate"] if t in lowered)
        if len(lowered) > 260 or score_complex >= 2:
            return "complex"
        if len(lowered) > 120 or score_moderate >= 2 or score_complex >= 1:
            return "moderate"
        return "simple"

    def _load_team_dir(self, team_dir: Path) -> Optional[Dict[str, Any]]:
        team_file = team_dir / "TEAM.md"
        if not team_file.exists():
            return None

        text = team_file.read_text(encoding="utf-8")
        frontmatter, body = _parse_frontmatter(text)
        if frontmatter is None:
            logger.warning("Invalid TEAM.md frontmatter in %s", team_file)
            return None

        team_key = str(frontmatter.get("id") or frontmatter.get("team_key") or team_dir.name).strip()
        team_key = _slugify(team_key)
        if not team_key or not _TEAM_NAME_RE.match(team_key) or "--" in team_key:
            logger.warning("Invalid team key in %s", team_file)
            return None

        rules_file = str(frontmatter.get("rules_file", "RULES.md") or "RULES.md").strip()
        worksteps_file = str(frontmatter.get("worksteps_file", "WORKSTEPS.md") or "WORKSTEPS.md").strip()

        rules_markdown = ""
        rules_path = team_dir / rules_file
        if rules_path.exists():
            rules_markdown = rules_path.read_text(encoding="utf-8").strip()
        elif body:
            rules_markdown = body

        worksteps_markdown = ""
        worksteps_path = team_dir / worksteps_file
        if worksteps_path.exists():
            worksteps_markdown = worksteps_path.read_text(encoding="utf-8").strip()

        team = {
            "id": team_key,
            "name": str(frontmatter.get("name", "")).strip(),
            "description": str(frontmatter.get("description", "")).strip(),
            "enabled": bool(frontmatter.get("enabled", True)),
            "priority": int(frontmatter.get("priority", 0) or 0),
            "team_type": str(frontmatter.get("team_type", "orchestrator_worker") or "orchestrator_worker"),
            "confidence_threshold": float(frontmatter.get("confidence_threshold", 0.62) or 0.62),
            "fit_policy": dict(frontmatter.get("fit_policy", {}) or {}),
            "budget_policy": dict(frontmatter.get("budget_policy", {}) or {}),
            "retry_policy": dict(frontmatter.get("retry_policy", {}) or {}),
            "tool_pool": list(frontmatter.get("tool_pool", []) or []),
            "required_capabilities": list(frontmatter.get("required_capabilities", []) or []),
            "capability_overrides": dict(frontmatter.get("capability_overrides", {}) or {}),
            "members": list(frontmatter.get("members", []) or []),
            "routes": list(frontmatter.get("routes", []) or []),
            "rules_markdown": rules_markdown,
            "worksteps_markdown": worksteps_markdown,
            "source_dir": str(team_dir),
            "rules_file": rules_file,
            "worksteps_file": worksteps_file,
            "writable": self._is_writable_team_dir(team_dir),
        }

        try:
            normalized = self._normalize_team(team)
            self._validate_team(normalized)
            return normalized
        except Exception as exc:
            logger.warning("Skipping invalid team dir %s: %s", team_dir, exc)
            return None

    def _write_team_dir(self, team_dir: Path, team: Dict[str, Any]) -> None:
        team_dir.mkdir(parents=True, exist_ok=True)

        rules_file = str(team.get("rules_file", "RULES.md") or "RULES.md")
        worksteps_file = str(team.get("worksteps_file", "WORKSTEPS.md") or "WORKSTEPS.md")

        frontmatter: Dict[str, Any] = {
            "id": team["id"],
            "name": team["name"],
            "description": team.get("description", ""),
            "enabled": bool(team.get("enabled", True)),
            "priority": int(team.get("priority", 0) or 0),
            "team_type": str(team.get("team_type", "orchestrator_worker") or "orchestrator_worker"),
            "confidence_threshold": float(team.get("confidence_threshold", 0.62) or 0.62),
            "fit_policy": dict(team.get("fit_policy", {}) or {}),
            "budget_policy": dict(team.get("budget_policy", {}) or {}),
            "retry_policy": dict(team.get("retry_policy", {}) or {}),
            "tool_pool": list(team.get("tool_pool", []) or []),
            "required_capabilities": list(team.get("required_capabilities", []) or []),
            "capability_overrides": dict(team.get("capability_overrides", {}) or {}),
            "routes": list(team.get("routes", []) or []),
            "members": list(team.get("members", []) or []),
            "rules_file": rules_file,
            "worksteps_file": worksteps_file,
        }

        yaml_block = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        content = f"---\n{yaml_block}\n---\n"
        (team_dir / "TEAM.md").write_text(content, encoding="utf-8")

        rules_text = str(team.get("rules_markdown", "") or "").strip() or "# Rules\n\nDefine hard constraints for this team."
        (team_dir / rules_file).write_text(rules_text + "\n", encoding="utf-8")

        worksteps_text = str(team.get("worksteps_markdown", "") or "").strip()
        if not worksteps_text:
            worksteps_text = _default_worksteps(team)
        (team_dir / worksteps_file).write_text(worksteps_text + "\n", encoding="utf-8")

    def _normalize_team(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        members = [self._normalize_member(m) for m in list(payload.get("members", []) or [])]
        routes = [self._normalize_route(r) for r in list(payload.get("routes", []) or [])]
        team_id = str(payload.get("id", "")).strip()
        team_type = str(payload.get("team_type", "orchestrator_worker") or "orchestrator_worker")

        return {
            "id": team_id,
            "name": str(payload.get("name", "")).strip(),
            "description": str(payload.get("description", "")).strip(),
            "enabled": bool(payload.get("enabled", True)),
            "priority": int(payload.get("priority", 0) or 0),
            "team_type": team_type,
            "confidence_threshold": float(payload.get("confidence_threshold", 0.62) or 0.62),
            "fit_policy": dict(payload.get("fit_policy", {}) or {}),
            "budget_policy": dict(payload.get("budget_policy", {}) or {}),
            "retry_policy": self._normalize_retry_policy(payload.get("retry_policy"), team_type=team_type),
            "tool_pool": [
                str(t).strip()
                for t in list(payload.get("tool_pool", []) or [])
                if str(t).strip()
            ],
            "required_capabilities": [
                str(item).strip()
                for item in list(payload.get("required_capabilities", []) or [])
                if str(item).strip()
            ],
            "capability_overrides": {
                str(key).strip(): [
                    str(p).strip()
                    for p in list(value or [])
                    if str(p).strip()
                ]
                for key, value in dict(payload.get("capability_overrides", {}) or {}).items()
                if str(key).strip()
            },
            "members": sorted(members, key=lambda m: int(m.get("order_index", 0))),
            "routes": routes,
            "rules_markdown": str(payload.get("rules_markdown", "") or "").strip(),
            "worksteps_markdown": str(payload.get("worksteps_markdown", "") or "").strip(),
            # Team-level verification command run after the full team completes.
            # Non-zero exit counts as a failure and consumes a retry slot.
            # Example: "pytest -q && curl -sf http://localhost:8000/health"
            "verification_command": str(payload.get("verification_command", "") or "").strip(),
            "source_dir": str(payload.get("source_dir", "") or ""),
            "rules_file": str(payload.get("rules_file", "RULES.md") or "RULES.md"),
            "worksteps_file": str(payload.get("worksteps_file", "WORKSTEPS.md") or "WORKSTEPS.md"),
            "writable": bool(payload.get("writable", False)),
        }

    def _normalize_member(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": payload.get("id"),
            "role": str(payload.get("role", "member")).strip() or "member",
            "objective_template": str(payload.get("objective_template", "")).strip(),
            "output_schema": dict(payload.get("output_schema", {}) or {}),
            "model": str(payload.get("model", "")).strip(),
            "tool_allowlist": [
                str(t).strip()
                for t in list(payload.get("tool_allowlist", []) or [])
                if str(t).strip()
            ],
            "skill_allowlist": [
                str(s).strip()
                for s in list(payload.get("skill_allowlist", []) or [])
                if str(s).strip()
            ],
            "workspace": str(payload.get("workspace", "")).strip(),
            "order_index": int(payload.get("order_index", 0) or 0),
            "max_tool_calls": int(payload.get("max_tool_calls", 0) or 0),
            "max_iterations": int(payload.get("max_iterations", 0) or 0),
            # Shell command run after this member completes (chain/parallel).
            # Non-zero exit aborts the chain and triggers a retry.
            # Example: "pytest tests/ -q" or "npx playwright test --reporter=line"
            "verification_command": str(payload.get("verification_command", "") or "").strip(),
        }

    def _normalize_retry_policy(self, payload: Any, *, team_type: str) -> Dict[str, Any]:
        defaults = dict(_DEFAULT_RETRY_POLICY_BY_TYPE.get(team_type, _DEFAULT_RETRY_POLICY_BY_TYPE["chain"]))
        raw = dict(payload or {}) if isinstance(payload, dict) else {}
        return {
            "max_retries": max(0, int(raw.get("max_retries", defaults["max_retries"]) or defaults["max_retries"])),
            "fail_on_defer": bool(raw.get("fail_on_defer", defaults["fail_on_defer"])),
            "require_blockers_section": bool(
                raw.get("require_blockers_section", defaults["require_blockers_section"])
            ),
            "enforce_shell_success": bool(
                raw.get("enforce_shell_success", defaults["enforce_shell_success"])
            ),
        }

    def _normalize_route(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": payload.get("id"),
            "route_type": str(payload.get("route_type", "keyword") or "keyword").strip(),
            "pattern_or_hint": str(payload.get("pattern_or_hint", "")).strip(),
            "weight": float(payload.get("weight", 1.0) or 1.0),
        }

    def _validate_team(self, team: Dict[str, Any]) -> None:
        if not team["id"]:
            raise ValueError("id is required")
        if not _TEAM_NAME_RE.match(str(team["id"])) or "--" in str(team["id"]):
            raise ValueError("id must be lowercase alphanumeric/hyphen slug")
        if not team["name"]:
            raise ValueError("name is required")
        if team["team_type"] not in {"chain", "parallel", "orchestrator_worker", "hybrid"}:
            raise ValueError("team_type must be one of chain|parallel|orchestrator_worker|hybrid")
        if not team["members"]:
            raise ValueError("at least one team member is required")
        if not team["routes"]:
            raise ValueError("at least one route is required")

        valid_skill_keys = {item["skill_key"] for item in self.list_skills(enabled_only=True)}

        for member in team["members"]:
            if not member["role"]:
                raise ValueError("member.role is required")
            for tool_name in member["tool_allowlist"]:
                if not self.tool_registry.get(tool_name):
                    raise ValueError(f"unknown tool in tool_allowlist: {tool_name}")
            for skill_key in member["skill_allowlist"]:
                if skill_key not in valid_skill_keys:
                    raise ValueError(f"unknown or disabled skill in skill_allowlist: {skill_key}")
        for tool_name in list(team.get("tool_pool", []) or []):
            if not self.tool_registry.get(tool_name):
                raise ValueError(f"unknown tool in tool_pool: {tool_name}")
        for item in list(team.get("required_capabilities", []) or []):
            if not str(item).strip():
                raise ValueError("required_capabilities entries must be non-empty")
        for cap_key, patterns in dict(team.get("capability_overrides", {}) or {}).items():
            if not str(cap_key).strip():
                raise ValueError("capability_overrides keys must be non-empty")
            if not isinstance(patterns, list):
                raise ValueError(f"capability_overrides.{cap_key} must be an array")
            for pattern in patterns:
                if not str(pattern).strip():
                    raise ValueError(f"capability_overrides.{cap_key} has empty pattern")

        for route in team["routes"]:
            if route["route_type"] not in {"keyword", "regex", "tag", "llm_router_hint"}:
                raise ValueError(f"unknown route_type: {route['route_type']}")
            if not route["pattern_or_hint"]:
                raise ValueError("route.pattern_or_hint is required")

    def _prepare_team_for_runtime(self, team: Dict[str, Any], allowed_tools: List[str]) -> Dict[str, Any]:
        allowed_global = [name for name in allowed_tools if self.tool_registry.get(name)]
        team_pool = [name for name in list(team.get("tool_pool", []) or []) if name in allowed_global]
        if not team_pool:
            team_pool = list(allowed_global)
        capability_preflight = self._resolve_capabilities_for_team(team=team, available_tools=team_pool)
        if list(team.get("required_capabilities", []) or []):
            effective_pool = [name for name in team_pool if name in set(capability_preflight["resolved_tools"])]
        else:
            effective_pool = list(team_pool)

        allowed_set = set(effective_pool)

        prepared = dict(team)
        runtime_members: List[Dict[str, Any]] = []
        for member in team.get("members", []):
            member_tools = set(member.get("tool_allowlist", []) or [])
            member_skills = list(member.get("skill_allowlist", []) or [])

            skill_tools: set[str] = set()
            for skill_key in member_skills:
                for tool_name in self._required_tools_from_skill(skill_key):
                    tool_name = str(tool_name).strip()
                    if tool_name:
                        skill_tools.add(tool_name)

            if member_tools and skill_tools:
                effective = member_tools.intersection(skill_tools)
            elif member_tools:
                effective = member_tools
            elif skill_tools:
                effective = skill_tools
            else:
                effective = set(effective_pool)

            effective = effective.intersection(allowed_set)
            ordered_effective = [name for name in effective_pool if name in effective]

            enriched = dict(member)
            enriched["effective_tools"] = ordered_effective
            runtime_members.append(enriched)

        prepared["members"] = runtime_members
        prepared["runtime_tool_pool"] = list(effective_pool)
        prepared["capability_preflight"] = capability_preflight
        return prepared

    def _resolve_capabilities_for_team(self, *, team: Dict[str, Any], available_tools: List[str]) -> Dict[str, Any]:
        required = [str(item).strip() for item in list(team.get("required_capabilities", []) or []) if str(item).strip()]
        capability_catalog = self._build_capability_catalog(
            available_tools=available_tools,
            capability_overrides=dict(team.get("capability_overrides", {}) or {}),
        )
        all_capability_keys = sorted(capability_catalog.keys())
        matched_keys: List[str] = []
        missing_patterns: List[str] = []

        for pattern in required:
            if pattern == "*":
                matches = list(all_capability_keys)
            else:
                lowered = pattern.lower()
                matches = [key for key in all_capability_keys if fnmatch.fnmatch(key, lowered)]
            if not matches:
                missing_patterns.append(pattern)
                continue
            for key in matches:
                if key not in matched_keys:
                    matched_keys.append(key)

        capabilities_without_tools = [
            key for key in matched_keys if len(capability_catalog.get(key, [])) == 0
        ]
        resolved_tools: List[str] = []
        for key in matched_keys:
            for name in capability_catalog.get(key, []):
                if name not in resolved_tools:
                    resolved_tools.append(name)

        ok = (
            len(missing_patterns) == 0
            and len(capabilities_without_tools) == 0
            and (len(resolved_tools) > 0 or len(required) == 0)
        )
        return {
            "ok": ok,
            "required": required,
            "matched_capabilities": matched_keys,
            "missing_patterns": missing_patterns,
            "capabilities_without_tools": capabilities_without_tools,
            "resolved_tools": resolved_tools,
            "catalog_keys": all_capability_keys,
        }

    def _build_capability_catalog(
        self,
        *,
        available_tools: List[str],
        capability_overrides: Dict[str, List[str]],
    ) -> Dict[str, List[str]]:
        merged_patterns: Dict[str, List[str]] = {
            key: list(patterns) for key, patterns in _CAPABILITY_TOOL_PATTERNS.items()
        }
        for key, patterns in (capability_overrides or {}).items():
            cap_key = str(key).strip().lower()
            if not cap_key:
                continue
            normalized_patterns = [str(item).strip() for item in list(patterns or []) if str(item).strip()]
            merged_patterns[cap_key] = normalized_patterns

        out: Dict[str, List[str]] = {}
        for cap_key, patterns in merged_patterns.items():
            matched = self._match_tool_patterns(patterns, available_tools)
            out[cap_key] = matched
        return out

    @staticmethod
    def _match_tool_patterns(patterns: List[str], available_tools: List[str]) -> List[str]:
        if not patterns:
            return []
        out: List[str] = []
        for tool_name in available_tools:
            lowered = str(tool_name).strip().lower()
            if any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in patterns):
                out.append(tool_name)
        return out

    def _required_tools_from_skill(self, skill_key: str) -> List[str]:
        skill = self.skill_registry.get(skill_key)
        if not skill:
            return []
        meta = skill.metadata.metadata or {}
        raw = meta.get("required_tools") or meta.get("tools") or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        out: List[str] = []
        for item in raw:
            name = str(item).strip()
            if name:
                out.append(name)
        return out

    def _score_team(self, team: Dict[str, Any], task: str) -> Tuple[float, Dict[str, Any]]:
        text = (task or "").lower()
        routes = team.get("routes", []) or []
        total_weight = 0.0
        matched_weight = 0.0
        matched: List[Dict[str, Any]] = []

        for route in routes:
            route_type = route.get("route_type", "keyword")
            pattern = str(route.get("pattern_or_hint", "")).strip()
            weight = float(route.get("weight", 1.0) or 1.0)
            if weight <= 0:
                continue
            total_weight += weight

            is_match = False
            if route_type in {"keyword", "tag", "llm_router_hint"}:
                is_match = pattern.lower() in text
            elif route_type == "regex":
                try:
                    is_match = re.search(pattern, task, flags=re.IGNORECASE) is not None
                except re.error:
                    is_match = False

            if is_match:
                matched_weight += weight
                matched.append({"route_type": route_type, "pattern": pattern, "weight": weight})

        if total_weight <= 0:
            score = 0.0
        else:
            score = matched_weight / total_weight

        words = {
            w
            for w in re.split(
                r"[^a-z0-9]+",
                (team.get("name", "") + " " + team.get("description", "")).lower(),
            )
            if len(w) >= 4
        }
        overlap = sum(1 for w in words if w in text)
        score = min(1.0, score + min(0.2, 0.03 * overlap))

        breakdown = {
            "matched_routes": matched,
            "matched_weight": matched_weight,
            "total_weight": total_weight,
            "lexical_overlap": overlap,
        }
        return score, breakdown

    def _normalize_selected_by(self, routing_mode: str) -> str:
        mode = (routing_mode or "").strip().lower()
        if mode in {"rule", "llm", "hybrid"}:
            return mode
        return "rule"

    def _is_writable_team_dir(self, team_dir: Path) -> bool:
        try:
            team_dir_resolved = team_dir.resolve()
            install_resolved = self._install_dir.resolve()
        except Exception:
            return False
        return install_resolved == team_dir_resolved or install_resolved in team_dir_resolved.parents

    def _install_from_git(self, source: str, name: Optional[str]) -> Tuple[bool, str, str]:
        parsed = urlparse(source)
        repo_name = Path(parsed.path).stem
        target_name = _slugify(name or repo_name)
        target_dir = self._install_dir / target_name
        if target_dir.exists():
            return False, f"Team '{target_name}' already exists", target_name

        try:
            subprocess.run(
                ["git", "clone", source, str(target_dir)],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            return False, f"Git clone failed: {exc.stderr}", target_name
        except FileNotFoundError:
            return False, "Git not found. Please install git.", target_name

        if not (target_dir / "TEAM.md").exists():
            shutil.rmtree(target_dir, ignore_errors=True)
            return False, "No TEAM.md found in repository root", target_name

        return True, f"Team '{target_name}' installed", target_name

    def _install_from_path(self, source_path: Path, name: Optional[str]) -> Tuple[bool, str, str]:
        resolved = source_path.expanduser().resolve()
        if not resolved.exists():
            return False, f"Path does not exist: {resolved}", ""

        target_name = _slugify(name or resolved.name)
        target_dir = self._install_dir / target_name
        if target_dir.exists():
            return False, f"Team '{target_name}' already exists", target_name

        if not (resolved / "TEAM.md").exists():
            return False, f"No TEAM.md found in {resolved}", target_name

        try:
            shutil.copytree(resolved, target_dir, symlinks=False)
        except Exception as exc:
            return False, f"Failed to copy team: {exc}", target_name

        return True, f"Team '{target_name}' installed", target_name


def _extract_keywords(text: str) -> List[str]:
    tokens = [t for t in re.split(r"[^a-z0-9]+", text) if len(t) >= 4]
    seen: set[str] = set()
    out: List[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _slug_to_title(text: str) -> str:
    words = [w.strip() for w in re.split(r"[^A-Za-z0-9]+", text) if w.strip()]
    return " ".join(w.capitalize() for w in words[:8])


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:64]


def _parse_frontmatter(text: str) -> tuple[Optional[Dict[str, Any]], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, ""
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
    if not isinstance(data, dict):
        return None, ""
    return data, body


def _default_team_dirs(config) -> List[Path]:
    dirs = [
        Path.cwd() / "agent_teams",
        Path.home() / ".umabot" / "agent-teams",
    ]
    cfg = getattr(config, "agent_teams", None)
    if cfg:
        for extra in list(getattr(cfg, "team_dirs", []) or []):
            dirs.append(Path(str(extra)).expanduser())
    return dirs


def _default_install_dir(config) -> str:
    cfg = getattr(config, "agent_teams", None)
    if cfg and str(getattr(cfg, "install_dir", "")).strip():
        return str(getattr(cfg, "install_dir"))
    return str(Path.home() / ".umabot" / "agent-teams")


def _default_worksteps(team: Dict[str, Any]) -> str:
    lines = ["# Worksteps", ""]
    for idx, member in enumerate(team.get("members", []) or []):
        role = str(member.get("role", f"Member {idx + 1}"))
        objective = str(member.get("objective_template", "")).strip()
        if objective:
            lines.append(f"{idx + 1}. **{role}**: {objective}")
        else:
            lines.append(f"{idx + 1}. **{role}**")
    return "\n".join(lines)
