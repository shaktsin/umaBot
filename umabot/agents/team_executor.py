from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from umabot.agents.agent import AgentRunResult, SpawnedAgent
from umabot.agents.orchestrator import DynamicOrchestrator
from umabot.tools.registry import Attachment
from umabot.tools.workspace import resolve_workspace, set_active_workspace


EventCallback = Callable[[str, Dict[str, Any]], Awaitable[None]]
UpdateCallback = Callable[[str], Awaitable[None]]
AgentToolGuard = Callable[[str, Dict[str, Any]], Awaitable[None]]
ApprovalCallback = Callable[[str, str], Awaitable[bool]]


class ChainMemberFailedError(Exception):
    """Raised when a chain team member fails, to abort the chain immediately."""

    def __init__(self, role: str, error_content: str) -> None:
        super().__init__(f"Chain member '{role}' failed: {error_content}")
        self.role = role
        self.error_content = error_content


class TeamExecutor:
    """Executes a selected team with workflow-specific behavior."""

    def __init__(
        self,
        *,
        orchestrator_llm,
        agent_llm,
        tool_registry,
        db,
        skill_registry,
        workspaces: List[Any],
        agent_context: str,
        on_event: EventCallback,
        send_update: UpdateCallback,
        max_orchestrator_iterations: int,
        max_agent_iterations: int,
        on_agent_tool_call: Optional[AgentToolGuard] = None,
        request_approval: Optional[ApprovalCallback] = None,
        disable_approval: bool = True,
    ) -> None:
        self.orchestrator_llm = orchestrator_llm
        self.agent_llm = agent_llm
        self.tool_registry = tool_registry
        self.db = db
        self.skill_registry = skill_registry
        self.workspaces = workspaces
        self.agent_context = agent_context
        self.on_event = on_event
        self.send_update = send_update
        self.max_orchestrator_iterations = max_orchestrator_iterations
        self.max_agent_iterations = max_agent_iterations
        self.on_agent_tool_call = on_agent_tool_call
        self.request_approval = request_approval
        self.disable_approval = disable_approval
        self._run_shell_telemetry: Dict[str, List[Dict[str, Any]]] = {}
        self._run_failure_fingerprints: Dict[str, set[str]] = {}

    async def execute(
        self,
        *,
        team: Dict[str, Any],
        task: str,
        history: List[Dict[str, Any]],
        run_id: str,
        complexity_class: str,
        selected_by: str,
        route_rationale: Dict[str, Any],
    ) -> Tuple[str, List[Attachment]]:
        team_type = str(team.get("team_type", "orchestrator_worker") or "orchestrator_worker")
        retry_policy = self._effective_retry_policy(team)
        max_retries = int(retry_policy.get("max_retries", 0) or 0)
        budget_snapshot = self._budget_snapshot(team, complexity_class)
        eff_orch_iters, eff_agent_iters = self._effective_budgets(team, complexity_class)

        self.db.create_agent_team_run(
            run_id=run_id,
            team_id=team.get("id"),
            status="running",
            complexity_class=complexity_class,
            selected_by=selected_by,
            budget_snapshot=budget_snapshot,
            route_rationale=route_rationale,
        )

        await self._emit(
            "team.exec.started",
            {
                "run_id": run_id,
                "team_id": team.get("id"),
                "team_name": team.get("name"),
                "team_type": team_type,
                "complexity_class": complexity_class,
                "budget": budget_snapshot,
                "max_retries": max_retries,
            },
        )

        capability_preflight = team.get("capability_preflight") if isinstance(team.get("capability_preflight"), dict) else {}
        if capability_preflight and not bool(capability_preflight.get("ok", False)):
            await self._emit(
                "team.exec.blocked_preflight",
                {
                    "run_id": run_id,
                    "team_id": team.get("id"),
                    "missing_patterns": list(capability_preflight.get("missing_patterns", []) or []),
                    "capabilities_without_tools": list(capability_preflight.get("capabilities_without_tools", []) or []),
                    "matched_capabilities": list(capability_preflight.get("matched_capabilities", []) or []),
                    "resolved_tools": list(capability_preflight.get("resolved_tools", []) or []),
                },
            )
            final_reply = self._capability_preflight_error(capability_preflight)
            self.db.complete_agent_team_run(run_id=run_id, status="failed")
            await self._emit(
                "team.exec.failed",
                {
                    "run_id": run_id,
                    "team_id": team.get("id"),
                    "status": "failed",
                },
            )
            return final_reply, []

        self._run_shell_telemetry[run_id] = []
        self._run_failure_fingerprints[run_id] = set()

        attempt = 0
        current_task = task
        final_reply = ""
        status = "failed"
        all_attachments: List[Attachment] = []
        execution_error: Exception | None = None
        try:
            try:
                while True:
                    if team_type in {"chain", "parallel"}:
                        final_reply, attachments = await self._run_structured_team(
                            team=team,
                            task=current_task,
                            run_id=run_id,
                            parallel=(team_type == "parallel"),
                            max_agent_iterations=eff_agent_iters,
                        )
                    else:
                        final_reply, attachments = await self._run_orchestrator_worker_team(
                            team=team,
                            task=current_task,
                            history=history,
                            run_id=run_id,
                            max_orchestrator_iterations=eff_orch_iters,
                            max_agent_iterations=eff_agent_iters,
                        )
                    all_attachments.extend(attachments)
                    status, final_reply = self._apply_completion_policy(
                        team=team,
                        final_reply=final_reply,
                        run_id=run_id,
                        retry_policy=retry_policy,
                    )

                    # Team-level verification command runs after the completion policy
                    # passes so it acts as an independent ground-truth check.
                    # Failure is treated as a blocked result and consumes a retry slot.
                    if status == "completed":
                        team_verify_cmd = str(team.get("verification_command", "") or "").strip()
                        if team_verify_cmd:
                            ok, vout = await self._run_verification_command(
                                command=team_verify_cmd, run_id=run_id, cwd=None
                            )
                            if not ok:
                                status = "failed"
                                final_reply = self._render_blocked_contract_reply(
                                    evidence=["Team-level verification command failed."],
                                    blockers=["verification_command_failed"],
                                    failed_commands=[team_verify_cmd],
                                    mitigations=[
                                        f"Fix the issue reported by the verification command and retry.",
                                        f"Verification output: {vout[:300]}",
                                    ],
                                )

                    if status == "completed":
                        break
                    if team_type in {"chain", "parallel"}:
                        break
                    if attempt >= max_retries:
                        break

                    failure_snapshot = self._build_failure_snapshot(run_id=run_id, final_reply=final_reply)
                    fingerprint = self._failure_fingerprint(failure_snapshot)
                    seen = self._run_failure_fingerprints.setdefault(run_id, set())
                    if fingerprint in seen:
                        final_reply = self._completion_contract_error(
                            "Retry loop detected repeated blocker fingerprint; no forward progress."
                        )
                        status = "failed"
                        break
                    seen.add(fingerprint)

                    attempt += 1
                    await self._emit(
                        "team.exec.retrying",
                        {
                            "run_id": run_id,
                            "team_id": team.get("id"),
                            "attempt": attempt,
                            "max_retries": max_retries,
                            "failure_categories": failure_snapshot.get("failure_categories", []),
                            "failed_commands": failure_snapshot.get("failed_commands", []),
                        },
                    )
                    # Clear shell telemetry for the new attempt so that old
                    # failed commands from a previous attempt are not counted as
                    # unresolved against the new attempt's quality gates.
                    # The repair task already carries forward the failure context.
                    self._run_shell_telemetry[run_id] = []
                    current_task = self._build_repair_task(
                        original_task=task,
                        previous_reply=final_reply,
                        failure_snapshot=failure_snapshot,
                        attempt=attempt,
                        max_retries=max_retries,
                    )
            except Exception as exc:
                execution_error = exc
                status = "failed"
                final_reply = self._render_blocked_contract_reply(
                    evidence=["Team execution aborted due to unhandled exception."],
                    blockers=["team_execution_exception"],
                    failed_commands=[],
                    mitigations=[str(exc)],
                )
        finally:
            self._run_shell_telemetry.pop(run_id, None)
            self._run_failure_fingerprints.pop(run_id, None)

        self.db.complete_agent_team_run(run_id=run_id, status=status)
        if status == "completed":
            await self._emit(
                "team.exec.completed",
                {
                    "run_id": run_id,
                    "team_id": team.get("id"),
                    "status": "completed",
                },
            )
        else:
            await self._emit(
                "team.exec.failed",
                {
                    "run_id": run_id,
                    "team_id": team.get("id"),
                    "status": status,
                    "error": str(execution_error) if execution_error else "",
                },
            )
        if execution_error:
            raise execution_error
        return final_reply, all_attachments

    async def _run_structured_team(
        self,
        *,
        team: Dict[str, Any],
        task: str,
        run_id: str,
        parallel: bool,
        max_agent_iterations: Optional[int] = None,
    ) -> Tuple[str, List[Attachment]]:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        root_node_id = f"team-{team.get('id', 'adhoc')}"

        members = list(team.get("members", []) or [])
        if not members:
            return "No team members defined.", []

        await self._emit(
            "multi_agent_run_started",
            {
                "run_id": run_id,
                "task": task,
                "status": "running",
                "started_at": now,
                "root_node_id": root_node_id,
                "nodes": [
                    {
                        "node_id": root_node_id,
                        "parent_node_id": "",
                        "role": f"team:{team.get('team_type', 'chain')}",
                        "status": "running",
                        "model": "",
                        "workspace": "",
                        "objective": team.get("description") or team.get("name", ""),
                    }
                ],
            },
        )

        collected: List[Tuple[str, AgentRunResult]] = []
        attachments: List[Attachment] = []

        if parallel:
            tasks = [
                self._run_member(
                    run_id=run_id,
                    parent_node_id=root_node_id,
                    member=member,
                    index=idx,
                    objective_input=task,
                    default_max_iterations=max_agent_iterations,
                )
                for idx, member in enumerate(members)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for idx, result in enumerate(results):
                role = members[idx].get("role", f"member-{idx + 1}")
                if isinstance(result, Exception):
                    collected.append((role, AgentRunResult(content=f"[{role}] failed: {result}", failed=True)))
                    continue
                collected.append((role, result))
                attachments.extend(result.attachments)
                self.db.add_agent_team_checkpoint(
                    run_id=run_id,
                    step_key=f"parallel:{idx + 1}",
                    state={"role": role, "status": "completed"},
                )
        else:
            current_context = task
            for idx, member in enumerate(members):
                role = member.get("role", f"member-{idx + 1}")
                result = await self._run_member(
                    run_id=run_id,
                    parent_node_id=root_node_id,
                    member=member,
                    index=idx,
                    objective_input=current_context,
                    default_max_iterations=max_agent_iterations,
                )
                # Abort the chain immediately on member failure instead of
                # feeding the error string as input to the next member.
                if result.failed:
                    raise ChainMemberFailedError(role, result.content)
                collected.append((role, result))
                attachments.extend(result.attachments)
                current_context = result.content or current_context

                # Run per-member verification command before marking step complete.
                verify_cmd = str(member.get("verification_command", "") or "").strip()
                if verify_cmd:
                    ws = resolve_workspace(str(member.get("workspace", "")).strip(), self.workspaces)
                    cwd = ws.path if ws and hasattr(ws, "path") else None
                    ok, vout = await self._run_verification_command(
                        command=verify_cmd, run_id=run_id, cwd=cwd
                    )
                    if not ok:
                        raise ChainMemberFailedError(
                            role,
                            f"Verification command failed: {verify_cmd}\n{vout[:500]}",
                        )

                self.db.add_agent_team_checkpoint(
                    run_id=run_id,
                    step_key=f"chain:{idx + 1}",
                    state={"role": role, "status": "completed"},
                )

        if parallel:
            lines = [f"[{role}]\n{result.content}" for role, result in collected]
            final_text = "\n\n".join(lines).strip() or "No result generated by team members."
        else:
            final_text = collected[-1][1].content if collected else "No result generated by team members."

        await self._emit(
            "multi_agent_run_completed",
            {
                "run_id": run_id,
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "summary": final_text,
            },
        )
        return final_text, attachments

    async def _run_member(
        self,
        *,
        run_id: str,
        parent_node_id: str,
        member: Dict[str, Any],
        index: int,
        objective_input: str,
        default_max_iterations: Optional[int] = None,
    ) -> AgentRunResult:
        role = str(member.get("role", f"member-{index + 1}"))
        objective_template = str(member.get("objective_template", "")).strip() or "Complete the assigned objective."
        objective = f"{objective_template}\n\nTask input:\n{objective_input}".strip()
        output_schema = member.get("output_schema", {}) or {}
        system_prompt = (
            f"You are {role}.\n"
            f"Respect the output contract.\n"
            f"Output schema hint: {output_schema}\n"
            "Keep responses concise and actionable."
        )

        skill_note = ""
        if member.get("skill_allowlist"):
            skill_note = "\nAllowed skills: " + ", ".join(member["skill_allowlist"])
        system_prompt += skill_note

        workspace_name = str(member.get("workspace", "")).strip()
        workspace = resolve_workspace(workspace_name, self.workspaces)
        set_active_workspace(workspace)
        node_id = f"team-member-{index + 1}"

        await self._emit(
            "multi_agent_node_added",
            {
                "run_id": run_id,
                "node_id": node_id,
                "parent_node_id": parent_node_id,
                "role": role,
                "status": "queued",
                "objective": objective_template,
                "workspace": workspace.name if workspace else "",
                "model": member.get("model", "") or getattr(self.agent_llm, "model", ""),
            },
        )
        await self._emit(
            "team.member.started",
            {
                "run_id": run_id,
                "node_id": node_id,
                "role": role,
                "allowed_skills": list(member.get("skill_allowlist", []) or []),
                "allowed_tools": list(member.get("effective_tools", []) or []),
            },
        )

        max_iterations = int(member.get("max_iterations", 0) or 0)
        if max_iterations <= 0:
            max_iterations = default_max_iterations if default_max_iterations and default_max_iterations > 0 else self.max_agent_iterations

        agent = SpawnedAgent(
            role=role,
            objective=objective,
            system_prompt=system_prompt,
            tool_names=list(member.get("effective_tools", []) or []),
            context="",
            llm_client=self.agent_llm,
            tool_registry=self.tool_registry,
            max_iterations=max_iterations,
            on_tool_call=self.on_agent_tool_call,
            on_event=self._handle_internal_event,
            run_id=run_id,
            agent_id=node_id,
            parent_agent_id=parent_node_id,
        )

        try:
            result = await agent.run()
            await self._emit(
                "team.member.completed",
                {
                    "run_id": run_id,
                    "node_id": node_id,
                    "role": role,
                    "status": "completed",
                },
            )
            return result
        except Exception as exc:
            await self._emit(
                "team.member.failed",
                {
                    "run_id": run_id,
                    "node_id": node_id,
                    "role": role,
                    "status": "failed",
                    "error": str(exc),
                },
            )
            return AgentRunResult(content=f"[{role}] failed: {exc}", failed=True)

    async def _run_orchestrator_worker_team(
        self,
        *,
        team: Dict[str, Any],
        task: str,
        history: List[Dict[str, Any]],
        run_id: str,
        max_orchestrator_iterations: Optional[int] = None,
        max_agent_iterations: Optional[int] = None,
    ) -> Tuple[str, List[Attachment]]:
        blueprint_lines: List[str] = [
            f"Use team '{team.get('name', '')}' as execution blueprint.",
            f"Team description: {team.get('description', '')}",
            "Member contracts:",
        ]
        for idx, member in enumerate(team.get("members", []) or []):
            blueprint_lines.append(
                f"{idx + 1}. role={member.get('role')} objective={member.get('objective_template', '')} "
                f"skills={member.get('skill_allowlist', [])} tools={member.get('effective_tools', [])}"
            )
        rules = str(team.get("rules_markdown", "") or "").strip()
        worksteps = str(team.get("worksteps_markdown", "") or "").strip()
        if rules:
            blueprint_lines.extend(
                [
                    "",
                    "Team rules (must be followed):",
                    rules,
                ]
            )
        if worksteps:
            blueprint_lines.extend(
                [
                    "",
                    "Team worksteps (must be executed and reflected in output):",
                    worksteps,
                ]
            )
        blueprint_lines.extend(
            [
                "",
                "Completion contract:",
                "- You must run a generic self-healing workflow when verification fails:",
                "  Diagnose root cause -> apply minimal patch -> re-run failing command -> re-run full gates.",
                "- Do not stop after first failure if more recovery budget is available.",
                "- Return normal human-readable output first.",
                "- Then append a fenced machine-readable block using this exact tag:",
                "  ```team_result",
                "  {",
                '    "status": "completed" | "blocked",',
                '    "evidence": ["..."],',
                '    "blockers": ["..."],',
                '    "failed_commands": ["..."],',
                '    "mitigations": ["..."]',
                "  }",
                "  ```",
                "- If status=blocked, include blockers/failed_commands/mitigations arrays.",
            ]
        )

        skill_context = "\n".join(blueprint_lines)
        available_tools = sorted({
            tool_name
            for member in (team.get("members", []) or [])
            for tool_name in (member.get("effective_tools", []) or [])
        })

        role_max_iterations = {
            str(m.get("role", "")): int(m.get("max_iterations", 0) or 0)
            for m in (team.get("members", []) or [])
            if m.get("role") and int(m.get("max_iterations", 0) or 0) > 0
        }

        orchestrator = DynamicOrchestrator(
            orchestrator_llm=self.orchestrator_llm,
            agent_llm=self.agent_llm,
            tool_registry=self.tool_registry,
            available_tool_names=available_tools,
            send_update_callback=self.send_update,
            request_approval_callback=self.request_approval,
            disable_approval=self.disable_approval,
            max_orchestrator_iterations=max_orchestrator_iterations if max_orchestrator_iterations and max_orchestrator_iterations > 0 else self.max_orchestrator_iterations,
            max_agent_iterations=max_agent_iterations if max_agent_iterations and max_agent_iterations > 0 else self.max_agent_iterations,
            role_max_iterations=role_max_iterations,
            skill_context=skill_context,
            skill_context_full="",
            agent_context=self.agent_context,
            skill_registry=self.skill_registry,
            workspaces=self.workspaces,
            run_id=run_id,
            on_event=self._handle_internal_event,
            on_agent_tool_call=self.on_agent_tool_call,
        )

        final_reply = await orchestrator.run(task=task, history=history)
        return final_reply, list(orchestrator.collected_attachments)

    async def _handle_internal_event(self, event_name: str, payload: Dict[str, Any]) -> None:
        self._record_shell_telemetry(event_name=event_name, payload=payload)
        self._record_agent_checkpoint(event_name=event_name, payload=payload)
        await self._emit(event_name, payload)

    async def _emit(self, event_name: str, payload: Dict[str, Any]) -> None:
        payload = dict(payload or {})
        payload.setdefault("timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.db.add_agent_team_event(run_id=str(payload.get("run_id", "")), event_name=event_name, payload=payload)
        await self.on_event(event_name, payload)

    def _budget_snapshot(self, team: Dict[str, Any], complexity_class: str) -> Dict[str, Any]:
        policy = dict(team.get("budget_policy", {}) or {})
        by_complexity = policy.get("budgets", {}) if isinstance(policy.get("budgets"), dict) else {}
        selected = by_complexity.get(complexity_class, {}) if isinstance(by_complexity, dict) else {}
        return {
            "complexity_class": complexity_class,
            "team_budget_policy": policy,
            "selected_budget": selected,
        }

    def _effective_budgets(self, team: Dict[str, Any], complexity_class: str) -> Tuple[int, int]:
        """Return (max_orchestrator_iterations, max_agent_iterations) from budget_policy for the given complexity class.

        Falls back to the executor-level defaults when no per-complexity override is present.
        YAML example:
          budget_policy:
            budgets:
              simple:   {max_orchestrator_iterations: 10, max_agent_iterations: 8}
              moderate: {max_orchestrator_iterations: 25, max_agent_iterations: 18}
              complex:  {max_orchestrator_iterations: 45, max_agent_iterations: 30}
        """
        policy = dict(team.get("budget_policy", {}) or {})
        by_complexity = policy.get("budgets", {}) if isinstance(policy.get("budgets"), dict) else {}
        selected = by_complexity.get(complexity_class, {}) if isinstance(by_complexity, dict) else {}
        orch = int(selected.get("max_orchestrator_iterations", 0) or 0)
        agent = int(selected.get("max_agent_iterations", 0) or 0)
        return (
            orch if orch > 0 else self.max_orchestrator_iterations,
            agent if agent > 0 else self.max_agent_iterations,
        )

    async def _run_verification_command(
        self,
        *,
        command: str,
        run_id: str,
        cwd: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Run a shell verification command and return (success, combined_output).

        Times out after 120 s. Any non-zero exit code or timeout counts as failure.
        """
        await self._emit(
            "team.verification.started",
            {"run_id": run_id, "command": command, "cwd": cwd or ""},
        )
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd or None,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            success = proc.returncode == 0
        except asyncio.TimeoutError:
            output = f"Verification command timed out after 120s: {command}"
            success = False
        except Exception as exc:
            output = f"Verification command failed to launch: {exc}"
            success = False

        await self._emit(
            "team.verification.completed",
            {
                "run_id": run_id,
                "command": command,
                "success": success,
                "output_preview": output[:400],
            },
        )
        return success, output

    def _effective_retry_policy(self, team: Dict[str, Any]) -> Dict[str, Any]:
        raw = dict(team.get("retry_policy", {}) or {})
        team_type = str(team.get("team_type", "orchestrator_worker") or "orchestrator_worker")
        defaults: Dict[str, Any] = {
            "max_retries": 0,
            "fail_on_defer": False,
            "require_blockers_section": False,
            "enforce_shell_success": False,
        }
        if team_type in {"orchestrator_worker", "hybrid"}:
            defaults.update(
                {
                    "max_retries": 2,
                    "fail_on_defer": True,
                    "require_blockers_section": True,
                    # Telemetry-based shell check is OFF by default for orchestrator_worker/hybrid.
                    # Orchestrators have LLM self-correction and the evidence requirement is the
                    # correct gate. Exact-command-string telemetry fires on intermediate failures
                    # that were fixed with different-but-equivalent commands, causing false rejections.
                    # Set enforce_shell_success: true explicitly in team YAML to opt-in.
                    "enforce_shell_success": False,
                }
            )
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

    def _apply_completion_policy(
        self,
        *,
        team: Dict[str, Any],
        final_reply: str,
        run_id: str = "",
        retry_policy: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        text = (final_reply or "").strip()
        if not text:
            return "failed", self._completion_contract_error("Empty final reply from team execution.")

        retry_policy = dict(retry_policy or self._effective_retry_policy(team))
        fail_on_defer = bool(retry_policy.get("fail_on_defer", False))
        require_blockers_section = bool(retry_policy.get("require_blockers_section", False))
        enforce_shell_success = bool(retry_policy.get("enforce_shell_success", False))
        if not fail_on_defer and not require_blockers_section and not enforce_shell_success:
            return "completed", final_reply

        team_type = str(team.get("team_type", "orchestrator_worker") or "orchestrator_worker")
        if team_type in {"chain", "parallel"} and not enforce_shell_success:
            return "completed", final_reply

        parsed = self._parse_team_result_block(text)
        if parsed is None and (fail_on_defer or require_blockers_section):
            return "failed", self._completion_contract_error(
                "Missing required ```team_result``` block in final reply."
            )
        if parsed is None:
            parsed = {}

        status = str(parsed.get("status", "")).strip().lower()
        if status and status not in {"completed", "blocked"}:
            return "failed", self._completion_contract_error(
                "Invalid team_result.status. Expected 'completed' or 'blocked'."
            )
        if not status:
            status = "completed"

        if status == "blocked":
            if require_blockers_section:
                if not self._is_non_empty_list(parsed.get("blockers")):
                    return "failed", self._completion_contract_error(
                        "team_result.blockers must be a non-empty array when status=blocked."
                    )
                if not self._is_non_empty_list(parsed.get("failed_commands")):
                    return "failed", self._completion_contract_error(
                        "team_result.failed_commands must be a non-empty array when status=blocked."
                    )
                if not self._is_non_empty_list(parsed.get("mitigations")):
                    return "failed", self._completion_contract_error(
                        "team_result.mitigations must be a non-empty array when status=blocked."
                    )
            return "failed", final_reply

        # status == completed
        if require_blockers_section and not self._is_non_empty_list(parsed.get("evidence")):
            return "failed", self._completion_contract_error(
                "team_result.evidence must be a non-empty array when status=completed. "
                "Include at least one concrete proof item (e.g. test output, URL, file path)."
            )
        if self._is_non_empty_list(parsed.get("failed_commands")):
            failed_commands = [str(item).strip() for item in list(parsed.get("failed_commands", []) or []) if str(item).strip()]
            return "failed", self._render_blocked_contract_reply(
                evidence=["Final reply marked completed but reported failed commands."],
                blockers=["completion_marked_with_failed_commands"],
                failed_commands=failed_commands,
                mitigations=[
                    "Re-run each failed command until exit code is 0.",
                    "Re-run full quality gates before returning completed.",
                ],
            )
        if enforce_shell_success:
            unresolved = self._unresolved_failed_commands(run_id=run_id)
            if unresolved:
                return "failed", self._render_blocked_contract_reply(
                    evidence=["Shell telemetry still has unresolved command failures."],
                    blockers=["unresolved_shell_failures"],
                    failed_commands=unresolved,
                    mitigations=[
                        "Diagnose root cause from stderr/traceback of failed commands.",
                        "Apply minimal patch and rerun failed commands.",
                        "Rerun full verification gates and return completed only when all pass.",
                    ],
                )
        return "completed", final_reply

    def _parse_team_result_block(self, text: str) -> Optional[Dict[str, Any]]:
        marker = "```team_result"
        start = text.find(marker)
        if start < 0:
            return None
        content_start = text.find("\n", start)
        if content_start < 0:
            return None
        end = text.find("```", content_start + 1)
        if end < 0:
            return None
        block = text[content_start + 1 : end].strip()
        if not block:
            return None
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    @staticmethod
    def _is_non_empty_list(value: Any) -> bool:
        return isinstance(value, list) and len(value) > 0

    def _completion_contract_error(self, reason: str) -> str:
        return (
            "Team execution failed completion contract.\n"
            f"Reason: {reason}\n\n"
            "Expected footer format:\n"
            "```team_result\n"
            "{\"status\":\"completed\",\"evidence\":[\"...\"]}\n"
            "```\n"
        )

    def _render_blocked_contract_reply(
        self,
        *,
        evidence: List[str],
        blockers: List[str],
        failed_commands: List[str],
        mitigations: List[str],
    ) -> str:
        payload = {
            "status": "blocked",
            "evidence": [item for item in evidence if str(item).strip()],
            "blockers": [item for item in blockers if str(item).strip()],
            "failed_commands": [item for item in failed_commands if str(item).strip()],
            "mitigations": [item for item in mitigations if str(item).strip()],
        }
        return (
            "Team execution blocked by verification policy.\n\n"
            "```team_result\n"
            f"{json.dumps(payload)}\n"
            "```\n"
        )

    def _capability_preflight_error(self, preflight: Dict[str, Any]) -> str:
        missing_patterns = [str(item) for item in list(preflight.get("missing_patterns", []) or []) if str(item).strip()]
        no_tools = [str(item) for item in list(preflight.get("capabilities_without_tools", []) or []) if str(item).strip()]
        blockers: List[str] = []
        blockers.extend([f"missing_capability:{item}" for item in missing_patterns])
        blockers.extend([f"missing_tool_for_capability:{item}" for item in no_tools])
        if not blockers:
            blockers.append("missing_capability:unknown")

        failed_commands: List[str] = []
        mitigations: List[str] = [
            "Install/enable required MCP tools and restart/reload gateway.",
            "Add matching tools to team.tool_pool or member tool_allowlist.",
            "Adjust required_capabilities patterns to match available capabilities.",
        ]
        payload = {
            "status": "blocked",
            "evidence": ["Team capability preflight failed before execution."],
            "blockers": blockers,
            "failed_commands": failed_commands,
            "mitigations": mitigations,
        }
        return (
            "Team capability preflight failed.\n\n"
            "```team_result\n"
            f"{json.dumps(payload)}\n"
            "```\n"
        )

    def _record_agent_checkpoint(self, *, event_name: str, payload: Dict[str, Any]) -> None:
        """Write a DB checkpoint when an orchestrator-spawned agent completes.

        This gives the orchestrator_worker path the same step-visibility that
        chain/parallel teams get via add_agent_team_checkpoint in _run_structured_team.
        """
        if event_name != "multi_agent_node_status":
            return
        if str(payload.get("status", "")) != "completed":
            return
        run_id = str(payload.get("run_id", "") or "")
        node_id = str(payload.get("node_id", "") or "")
        role = str(payload.get("role", "") or "")
        # Skip the orchestrator root node itself — only record worker agents.
        if not run_id or not node_id or node_id == "orchestrator":
            return
        try:
            self.db.add_agent_team_checkpoint(
                run_id=run_id,
                step_key=f"agent:{node_id}",
                state={"role": role, "status": "completed", "node_id": node_id},
            )
        except Exception:
            pass

    def _record_shell_telemetry(self, *, event_name: str, payload: Dict[str, Any]) -> None:
        if event_name != "multi_agent_node_log":
            return
        run_id = str(payload.get("run_id", "") or "")
        if not run_id:
            return
        event_type = str(payload.get("event_type", "") or "")
        details = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        tool = str(details.get("tool", "") or "")
        if tool != "shell.run":
            return

        if event_type == "agent.tool.result":
            cmd = str(details.get("cmd", "") or "")
            cwd = str(details.get("cwd", "") or "")
            exit_code = details.get("exit_code")
            timed_out = bool(details.get("timed_out", False))
            content_preview = str(details.get("content_preview", "") or "")
            success = isinstance(exit_code, int) and int(exit_code) == 0 and not timed_out
            self._run_shell_telemetry.setdefault(run_id, []).append(
                {
                    "node_id": str(payload.get("node_id", "") or ""),
                    "role": str(payload.get("role", "") or ""),
                    "cmd": cmd,
                    "cwd": cwd,
                    "exit_code": int(exit_code) if isinstance(exit_code, int) else None,
                    "timed_out": timed_out,
                    "success": success,
                    "content_preview": content_preview,
                }
            )
            return

        if event_type == "agent.tool.error":
            self._run_shell_telemetry.setdefault(run_id, []).append(
                {
                    "node_id": str(payload.get("node_id", "") or ""),
                    "role": str(payload.get("role", "") or ""),
                    "cmd": "",
                    "cwd": "",
                    "exit_code": None,
                    "timed_out": False,
                    "success": False,
                    "content_preview": str(details.get("error", "") or ""),
                }
            )

    def _unresolved_failed_commands(self, *, run_id: str) -> List[str]:
        latest_by_cmd: Dict[str, Dict[str, Any]] = {}
        for item in list(self._run_shell_telemetry.get(run_id, [])):
            cmd = str(item.get("cmd", "") or "").strip()
            if not cmd:
                continue
            latest_by_cmd[cmd] = item

        unresolved: List[str] = []
        for cmd, item in latest_by_cmd.items():
            if bool(item.get("success", False)):
                continue
            exit_code = item.get("exit_code")
            suffix = f" (exit_code={exit_code})" if exit_code is not None else ""
            unresolved.append(f"{cmd}{suffix}")
        return unresolved[:12]

    def _build_failure_snapshot(self, *, run_id: str, final_reply: str) -> Dict[str, Any]:
        team_result = self._parse_team_result_block(final_reply) or {}
        failed_commands = self._unresolved_failed_commands(run_id=run_id)
        failed_shell = [
            item
            for item in list(self._run_shell_telemetry.get(run_id, []))
            if str(item.get("cmd", "") or "").strip() in {cmd.split(" (exit_code=")[0] for cmd in failed_commands}
        ]
        blocked_cmds = team_result.get("failed_commands") if isinstance(team_result, dict) else []
        if isinstance(blocked_cmds, list):
            for cmd in blocked_cmds:
                text = str(cmd).strip()
                if text and text not in failed_commands:
                    failed_commands.append(text)
        failure_texts: List[str] = []
        blockers = team_result.get("blockers") if isinstance(team_result, dict) else []
        if isinstance(blockers, list):
            failure_texts.extend(str(item) for item in blockers if str(item).strip())
        for item in failed_shell:
            preview = str(item.get("content_preview", "") or "").strip()
            if preview:
                failure_texts.append(preview)
        categories = sorted({self._classify_failure_text(text) for text in failure_texts if text.strip()})
        if not categories:
            categories = ["unknown"]
        return {
            "failed_commands": failed_commands[:12],
            "failure_categories": categories,
            "failure_texts": failure_texts[:12],
            "team_result": team_result if isinstance(team_result, dict) else {},
        }

    def _failure_fingerprint(self, failure_snapshot: Dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "failed_commands": failure_snapshot.get("failed_commands", []),
                "failure_categories": failure_snapshot.get("failure_categories", []),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _build_repair_task(
        self,
        *,
        original_task: str,
        previous_reply: str,
        failure_snapshot: Dict[str, Any],
        attempt: int,
        max_retries: int,
    ) -> str:
        snapshot_json = json.dumps(failure_snapshot, indent=2, ensure_ascii=True)
        return (
            f"Retry attempt {attempt}/{max_retries}.\n\n"
            "You are in self-healing mode. Follow this generic loop strictly:\n"
            "1) Diagnose the root cause from concrete command failures.\n"
            "2) Apply the smallest patch that addresses that root cause.\n"
            "3) Re-run the failing command(s).\n"
            "4) Re-run full verification gates before claiming completion.\n"
            "5) If still blocked, emit exact blockers with failed commands and mitigations.\n\n"
            "Failure telemetry from previous attempt:\n"
            f"{snapshot_json}\n\n"
            "Previous final reply:\n"
            f"{previous_reply}\n\n"
            "Original user request:\n"
            f"{original_task}\n"
        )

    @staticmethod
    def _classify_failure_text(text: str) -> str:
        lowered = (text or "").lower()
        if "no module named" in lowered or "importerror" in lowered:
            return "import_path"
        if "alembic" in lowered or "migration" in lowered or "no such table" in lowered:
            return "database_migration"
        if "readonly" in lowered or "permission denied" in lowered:
            return "filesystem_permissions"
        if "connection refused" in lowered or "timed out" in lowered:
            return "service_connectivity"
        if "assert" in lowered or "failed" in lowered or "traceback" in lowered:
            return "test_failure"
        if "could not find a version" in lowered or "no matching distribution" in lowered:
            return "dependency_install"
        return "unknown"
