from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from umabot.agents.agent import AgentRunResult
from umabot.agents.team_executor import ChainMemberFailedError, TeamExecutor


def _executor() -> TeamExecutor:
    return TeamExecutor(
        orchestrator_llm=None,
        agent_llm=None,
        tool_registry=None,
        db=None,
        skill_registry=None,
        workspaces=[],
        agent_context="",
        on_event=lambda *_args, **_kwargs: None,
        send_update=lambda *_args, **_kwargs: None,
        max_orchestrator_iterations=1,
        max_agent_iterations=1,
    )


def test_apply_completion_policy_fails_when_team_result_block_is_missing() -> None:
    team = {
        "team_type": "orchestrator_worker",
        "retry_policy": {"fail_on_defer": True, "require_blockers_section": True},
    }
    final_reply = "Implemented all requested changes."

    status, reply = _executor()._apply_completion_policy(team=team, final_reply=final_reply)

    assert status == "failed"
    assert "completion contract" in reply.lower()


def test_apply_completion_policy_defaults_enforce_contract_for_orchestrator_worker() -> None:
    team = {
        "team_type": "orchestrator_worker",
        "retry_policy": {},
    }
    final_reply = "Implemented all requested changes."

    status, reply = _executor()._apply_completion_policy(team=team, final_reply=final_reply)

    assert status == "failed"
    assert "missing required ```team_result``` block" in reply.lower()


def test_apply_completion_policy_keeps_completed_for_structured_completed_status() -> None:
    team = {
        "team_type": "orchestrator_worker",
        "retry_policy": {"fail_on_defer": True, "require_blockers_section": True},
    }
    final_reply = (
        "Implemented all requested changes and validated with passing tests.\n\n"
        "```team_result\n"
        "{\"status\":\"completed\",\"evidence\":[\"pytest -q passed\"]}\n"
        "```"
    )

    status, reply = _executor()._apply_completion_policy(team=team, final_reply=final_reply)

    assert status == "completed"
    assert reply == final_reply


def test_apply_completion_policy_requires_blocker_arrays_when_blocked() -> None:
    team = {
        "team_type": "orchestrator_worker",
        "retry_policy": {"fail_on_defer": True, "require_blockers_section": True},
    }
    final_reply = (
        "Unable to continue.\n\n"
        "```team_result\n"
        "{\"status\":\"blocked\",\"evidence\":[\"attempted run\"]}\n"
        "```"
    )

    status, reply = _executor()._apply_completion_policy(team=team, final_reply=final_reply)

    assert status == "failed"
    assert "team_result.blockers" in reply


def test_apply_completion_policy_fails_completed_when_shell_telemetry_has_unresolved_failures() -> None:
    executor = _executor()
    executor._run_shell_telemetry["run-telemetry"] = [
        {
            "cmd": "pytest -q",
            "exit_code": 1,
            "success": False,
            "timed_out": False,
            "content_preview": "assert failed",
        }
    ]
    team = {
        "team_type": "orchestrator_worker",
        "retry_policy": {
            "fail_on_defer": True,
            "require_blockers_section": True,
            "enforce_shell_success": True,
        },
    }
    final_reply = (
        "Implemented all requested changes and validated.\n\n"
        "```team_result\n"
        "{\"status\":\"completed\",\"evidence\":[\"done\"]}\n"
        "```"
    )

    status, reply = executor._apply_completion_policy(
        team=team,
        final_reply=final_reply,
        run_id="run-telemetry",
    )

    assert status == "failed"
    assert "\"unresolved_shell_failures\"" in reply


class _FakeDB:
    def __init__(self) -> None:
        self.events = []
        self.completed_status = None
        self.checkpoints = []

    def create_agent_team_run(self, **_kwargs) -> None:
        return None

    def add_agent_team_event(self, **kwargs) -> None:
        self.events.append(kwargs)

    def complete_agent_team_run(self, **kwargs) -> None:
        self.completed_status = kwargs.get("status")

    def add_agent_team_checkpoint(self, **kwargs) -> None:
        self.checkpoints.append(kwargs)


def test_execute_retries_once_then_completes() -> None:
    db = _FakeDB()
    seen_tasks = []
    emitted_events = []
    replies = [
        (
            "Attempt failed.\n\n"
            "```team_result\n"
            '{"status":"blocked","evidence":["ran checks"],"blockers":["tests failed"],'
            '"failed_commands":["pytest -q"],"mitigations":["fix test failure"]}\n'
            "```"
        ),
        (
            "Patched and verified.\n\n"
            "```team_result\n"
            '{"status":"completed","evidence":["pytest -q passed"]}\n'
            "```"
        ),
    ]

    team = {
        "id": "fullstack",
        "team_type": "orchestrator_worker",
        "retry_policy": {
            "fail_on_defer": True,
            "require_blockers_section": True,
            "max_retries": 2,
        },
    }

    executor = TeamExecutor(
        orchestrator_llm=None,
        agent_llm=None,
        tool_registry=None,
        db=db,
        skill_registry=None,
        workspaces=[],
        agent_context="",
        on_event=lambda name, payload: _capture_event(emitted_events, name, payload),
        send_update=lambda *_args, **_kwargs: _noop(),
        max_orchestrator_iterations=1,
        max_agent_iterations=1,
    )

    async def _fake_run(*, team, task, history, run_id, **_kw):
        seen_tasks.append(task)
        return replies.pop(0), []

    executor._run_orchestrator_worker_team = _fake_run  # type: ignore[method-assign]

    reply, attachments = asyncio.run(
        executor.execute(
            team=team,
            task="Build stack",
            history=[],
            run_id="run-1",
            complexity_class="hard",
            selected_by="score",
            route_rationale={},
        )
    )

    assert attachments == []
    assert "status\":\"completed\"" in reply
    assert db.completed_status == "completed"
    assert any(name == "team.exec.retrying" for name, _payload in emitted_events)
    assert len(seen_tasks) == 2
    assert "Retry attempt 1/2." in seen_tasks[1]


def test_execute_stops_on_repeated_failure_fingerprint() -> None:
    db = _FakeDB()
    emitted_events = []
    seen_tasks = []
    blocked_reply = (
        "Still blocked.\n\n"
        "```team_result\n"
        '{"status":"blocked","evidence":["ran checks"],"blockers":["same failure"],'
        '"failed_commands":["pytest -q"],"mitigations":["none"]}\n'
        "```"
    )

    team = {
        "id": "fullstack",
        "team_type": "orchestrator_worker",
        "retry_policy": {
            "fail_on_defer": True,
            "require_blockers_section": True,
            "max_retries": 3,
        },
    }

    executor = TeamExecutor(
        orchestrator_llm=None,
        agent_llm=None,
        tool_registry=None,
        db=db,
        skill_registry=None,
        workspaces=[],
        agent_context="",
        on_event=lambda name, payload: _capture_event(emitted_events, name, payload),
        send_update=lambda *_args, **_kwargs: _noop(),
        max_orchestrator_iterations=1,
        max_agent_iterations=1,
    )

    async def _fake_run(*, team, task, history, run_id, **_kw):
        seen_tasks.append(task)
        return blocked_reply, []

    executor._run_orchestrator_worker_team = _fake_run  # type: ignore[method-assign]

    reply, attachments = asyncio.run(
        executor.execute(
            team=team,
            task="Build stack",
            history=[],
            run_id="run-2",
            complexity_class="hard",
            selected_by="score",
            route_rationale={},
        )
    )

    assert attachments == []
    assert "repeated blocker fingerprint" in reply.lower()
    assert db.completed_status == "failed"
    retry_events = [name for name, _payload in emitted_events if name == "team.exec.retrying"]
    assert len(retry_events) == 1
    assert len(seen_tasks) == 2


def test_execute_blocks_on_capability_preflight_failure() -> None:
    db = _FakeDB()
    emitted_events = []
    team = {
        "id": "browser-team",
        "team_type": "orchestrator_worker",
        "retry_policy": {"fail_on_defer": True, "require_blockers_section": True, "max_retries": 1},
        "capability_preflight": {
            "ok": False,
            "missing_patterns": ["screenshot_*"],
            "capabilities_without_tools": [],
            "matched_capabilities": [],
            "resolved_tools": [],
        },
    }

    executor = TeamExecutor(
        orchestrator_llm=None,
        agent_llm=None,
        tool_registry=None,
        db=db,
        skill_registry=None,
        workspaces=[],
        agent_context="",
        on_event=lambda name, payload: _capture_event(emitted_events, name, payload),
        send_update=lambda *_args, **_kwargs: _noop(),
        max_orchestrator_iterations=1,
        max_agent_iterations=1,
    )

    reply, attachments = asyncio.run(
        executor.execute(
            team=team,
            task="Capture screenshots",
            history=[],
            run_id="run-3",
            complexity_class="complex",
            selected_by="hybrid",
            route_rationale={},
        )
    )

    assert attachments == []
    assert "missing_capability:screenshot_*" in reply
    assert db.completed_status == "failed"
    assert any(name == "team.exec.blocked_preflight" for name, _payload in emitted_events)


def test_execute_finalizes_failed_run_when_member_execution_raises() -> None:
    db = _FakeDB()
    emitted_events = []

    team = {
        "id": "fullstack",
        "team_type": "orchestrator_worker",
        "retry_policy": {
            "fail_on_defer": True,
            "require_blockers_section": True,
            "max_retries": 1,
        },
    }

    executor = TeamExecutor(
        orchestrator_llm=None,
        agent_llm=None,
        tool_registry=None,
        db=db,
        skill_registry=None,
        workspaces=[],
        agent_context="",
        on_event=lambda name, payload: _capture_event(emitted_events, name, payload),
        send_update=lambda *_args, **_kwargs: _noop(),
        max_orchestrator_iterations=1,
        max_agent_iterations=1,
    )

    async def _failing_run(*, team, task, history, run_id, **_kw):
        raise RuntimeError("openai 429 after retries")

    executor._run_orchestrator_worker_team = _failing_run  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="openai 429 after retries"):
        asyncio.run(
            executor.execute(
                team=team,
                task="Build stack",
                history=[],
                run_id="run-4",
                complexity_class="hard",
                selected_by="score",
                route_rationale={},
            )
        )

    assert db.completed_status == "failed"
    failed_events = [payload for name, payload in emitted_events if name == "team.exec.failed"]
    assert failed_events
    assert failed_events[-1].get("error") == "openai 429 after retries"


async def _noop() -> None:
    return None


async def _capture_event(bucket, name, payload) -> None:
    bucket.append((name, payload))


# ---------------------------------------------------------------------------
# Evidence validation (Fix 5)
# ---------------------------------------------------------------------------

def test_apply_completion_policy_fails_when_evidence_is_empty() -> None:
    team = {
        "team_type": "orchestrator_worker",
        "retry_policy": {"fail_on_defer": True, "require_blockers_section": True},
    }
    final_reply = (
        "Done.\n\n"
        "```team_result\n"
        "{\"status\":\"completed\",\"evidence\":[]}\n"
        "```"
    )
    status, reply = _executor()._apply_completion_policy(team=team, final_reply=final_reply)
    assert status == "failed"
    assert "evidence" in reply.lower()


def test_apply_completion_policy_passes_with_non_empty_evidence() -> None:
    team = {
        "team_type": "orchestrator_worker",
        "retry_policy": {"fail_on_defer": True, "require_blockers_section": True},
    }
    final_reply = (
        "Done.\n\n"
        "```team_result\n"
        "{\"status\":\"completed\",\"evidence\":[\"pytest passed\"]}\n"
        "```"
    )
    status, _ = _executor()._apply_completion_policy(team=team, final_reply=final_reply)
    assert status == "completed"


# ---------------------------------------------------------------------------
# Budget policy wiring (Fix 2)
# ---------------------------------------------------------------------------

def test_effective_budgets_uses_complexity_class_override() -> None:
    executor = _executor()
    executor.max_orchestrator_iterations = 20
    executor.max_agent_iterations = 15
    team = {
        "budget_policy": {
            "budgets": {
                "complex": {"max_orchestrator_iterations": 45, "max_agent_iterations": 30},
            }
        }
    }
    orch, agent = executor._effective_budgets(team, "complex")
    assert orch == 45
    assert agent == 30


def test_effective_budgets_falls_back_to_defaults_when_no_match() -> None:
    executor = _executor()
    executor.max_orchestrator_iterations = 20
    executor.max_agent_iterations = 15
    team: dict = {}
    orch, agent = executor._effective_budgets(team, "complex")
    assert orch == 20
    assert agent == 15


# ---------------------------------------------------------------------------
# Chain abort on member failure (Fix 3)
# ---------------------------------------------------------------------------

def test_chain_member_failed_error_is_exported() -> None:
    err = ChainMemberFailedError("Builder", "tool crashed")
    assert "Builder" in str(err)
    assert err.role == "Builder"


def test_agent_run_result_failed_flag() -> None:
    result = AgentRunResult(content="error", failed=True)
    assert result.failed is True
    assert AgentRunResult(content="ok").failed is False


def test_execute_marks_failed_when_chain_member_raises() -> None:
    """ChainMemberFailedError raised inside _run_structured_team must surface as a failed run."""
    db = _FakeDB()
    db.add_agent_team_checkpoint = lambda **_kw: None  # chain path uses this
    emitted_events = []

    team = {
        "id": "chain-team",
        "team_type": "chain",
        "retry_policy": {},
        "members": [{"role": "Builder", "objective_template": "build it"}],
    }

    executor = TeamExecutor(
        orchestrator_llm=None,
        agent_llm=None,
        tool_registry=None,
        db=db,
        skill_registry=None,
        workspaces=[],
        agent_context="",
        on_event=lambda name, payload: _capture_event(emitted_events, name, payload),
        send_update=lambda *_args, **_kwargs: _noop(),
        max_orchestrator_iterations=1,
        max_agent_iterations=1,
    )

    async def _failing_structured(**_kwargs):
        raise ChainMemberFailedError("Builder", "tool crashed")

    executor._run_structured_team = _failing_structured  # type: ignore[method-assign]

    with pytest.raises(ChainMemberFailedError):
        asyncio.run(
            executor.execute(
                team=team,
                task="build app",
                history=[],
                run_id="run-chain-1",
                complexity_class="moderate",
                selected_by="score",
                route_rationale={},
            )
        )

    assert db.completed_status == "failed"


# ---------------------------------------------------------------------------
# Team-level verification command (Fix 6b)
# ---------------------------------------------------------------------------

def test_execute_fails_when_team_verification_command_fails() -> None:
    db = _FakeDB()
    emitted_events = []
    completed_reply = (
        "All done.\n\n"
        "```team_result\n"
        '{"status":"completed","evidence":["pytest passed"]}\n'
        "```"
    )

    team = {
        "id": "webapp",
        "team_type": "orchestrator_worker",
        "verification_command": "pytest -q",
        "retry_policy": {
            "fail_on_defer": True,
            "require_blockers_section": True,
            "max_retries": 0,
        },
    }

    executor = TeamExecutor(
        orchestrator_llm=None,
        agent_llm=None,
        tool_registry=None,
        db=db,
        skill_registry=None,
        workspaces=[],
        agent_context="",
        on_event=lambda name, payload: _capture_event(emitted_events, name, payload),
        send_update=lambda *_args, **_kwargs: _noop(),
        max_orchestrator_iterations=1,
        max_agent_iterations=1,
    )

    async def _fake_run(*, team, task, history, run_id, **_kw):
        return completed_reply, []

    executor._run_orchestrator_worker_team = _fake_run  # type: ignore[method-assign]

    # Patch _run_verification_command to simulate a failing test suite.
    async def _fake_verify(*, command, run_id, cwd=None):
        return False, "FAILED: 3 tests failed"

    executor._run_verification_command = _fake_verify  # type: ignore[method-assign]

    reply, _ = asyncio.run(
        executor.execute(
            team=team,
            task="Build webapp",
            history=[],
            run_id="run-verify-1",
            complexity_class="complex",
            selected_by="score",
            route_rationale={},
        )
    )

    assert db.completed_status == "failed"
    # execute() must have emitted a failure event — the key observable outcome.
    fail_events = [name for name, _ in emitted_events if name == "team.exec.failed"]
    assert len(fail_events) >= 1


def test_execute_succeeds_when_team_verification_command_passes() -> None:
    db = _FakeDB()
    completed_reply = (
        "All done.\n\n"
        "```team_result\n"
        '{"status":"completed","evidence":["pytest passed"]}\n'
        "```"
    )

    team = {
        "id": "webapp",
        "team_type": "orchestrator_worker",
        "verification_command": "pytest -q",
        "retry_policy": {
            "fail_on_defer": True,
            "require_blockers_section": True,
            "max_retries": 0,
        },
    }

    executor = TeamExecutor(
        orchestrator_llm=None,
        agent_llm=None,
        tool_registry=None,
        db=db,
        skill_registry=None,
        workspaces=[],
        agent_context="",
        on_event=lambda name, payload: _capture_event([], name, payload),
        send_update=lambda *_args, **_kwargs: _noop(),
        max_orchestrator_iterations=1,
        max_agent_iterations=1,
    )

    async def _fake_run(*, team, task, history, run_id, **_kw):
        return completed_reply, []

    executor._run_orchestrator_worker_team = _fake_run  # type: ignore[method-assign]

    async def _fake_verify(*, command, run_id, cwd=None):
        return True, "1 passed"

    executor._run_verification_command = _fake_verify  # type: ignore[method-assign]

    reply, _ = asyncio.run(
        executor.execute(
            team=team,
            task="Build webapp",
            history=[],
            run_id="run-verify-2",
            complexity_class="complex",
            selected_by="score",
            route_rationale={},
        )
    )

    assert db.completed_status == "completed"
    assert "status\":\"completed\"" in reply


# ---------------------------------------------------------------------------
# Truncation flag (Fix 1)
# ---------------------------------------------------------------------------

def test_agent_run_result_truncated_flag() -> None:
    result = AgentRunResult(content="partial work", truncated=True)
    assert result.truncated is True
    assert AgentRunResult(content="full work").truncated is False


# ---------------------------------------------------------------------------
# Shell telemetry reset between retries (regression guard)
# ---------------------------------------------------------------------------

def test_shell_telemetry_is_cleared_between_retry_attempts() -> None:
    """Failed commands from attempt 1 must NOT poison attempt 2's quality gates.

    Scenario: attempt 1 has a shell failure; attempt 2 produces a valid completed
    team_result with no failed commands.  Without the telemetry reset, attempt 2
    would be rejected because the old failure is still in telemetry.
    """
    db = _FakeDB()
    emitted_events = []
    call_count = [0]

    attempt1_reply = (
        "Build failed.\n\n"
        "```team_result\n"
        '{"status":"blocked","evidence":["ran build"],"blockers":["build failed"],'
        '"failed_commands":["npm run build"],"mitigations":["fix build"]}\n'
        "```"
    )
    attempt2_reply = (
        "All done.\n\n"
        "```team_result\n"
        '{"status":"completed","evidence":["npm run build passed","pytest passed"]}\n'
        "```"
    )

    team = {
        "id": "fullstack",
        "team_type": "orchestrator_worker",
        "retry_policy": {
            "fail_on_defer": True,
            "require_blockers_section": True,
            "enforce_shell_success": True,
            "max_retries": 2,
        },
    }

    executor = TeamExecutor(
        orchestrator_llm=None,
        agent_llm=None,
        tool_registry=None,
        db=db,
        skill_registry=None,
        workspaces=[],
        agent_context="",
        on_event=lambda name, payload: _capture_event(emitted_events, name, payload),
        send_update=lambda *_args, **_kwargs: _noop(),
        max_orchestrator_iterations=1,
        max_agent_iterations=1,
    )

    async def _fake_run(*, team, task, history, run_id, **_kw):
        call_count[0] += 1
        if call_count[0] == 1:
            # Inject a shell failure into telemetry to simulate attempt 1
            executor._run_shell_telemetry.setdefault(run_id, []).append({
                "cmd": "npm run build",
                "exit_code": 1,
                "success": False,
                "timed_out": False,
                "content_preview": "build error",
            })
            return attempt1_reply, []
        # Attempt 2: clean run — no new shell failures injected
        return attempt2_reply, []

    executor._run_orchestrator_worker_team = _fake_run  # type: ignore[method-assign]

    reply, _ = asyncio.run(
        executor.execute(
            team=team,
            task="Build app",
            history=[],
            run_id="run-telemetry-reset",
            complexity_class="complex",
            selected_by="score",
            route_rationale={},
        )
    )

    # Without the telemetry reset, attempt 2 would still see "npm run build" as
    # unresolved and return "failed".  With the fix it should complete.
    assert db.completed_status == "completed", f"Expected completed but got {db.completed_status!r}. Reply: {reply[:200]}"
    assert "status\":\"completed\"" in reply
