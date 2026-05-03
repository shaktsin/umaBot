from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import logging
import mimetypes
import re
import secrets
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from umabot.agents import DynamicOrchestrator, TeamExecutor, TeamRegistry
from umabot.intent import IntentResult, detect_intent, intent_context_block
from umabot.llm import ClaudeClient, GeminiClient, OpenAIClient
from umabot.llm.rate_limiter import TokenBucket
from umabot.llm.scheduler import LLMScheduler, P0, P1, P2
from umabot.policy import DeclarativePolicyEngine, PolicyEngine, RuleContext
from umabot.security import SecurityPolicy, mask_secrets
from umabot.security.ssrf import check_ssrf, SSRFError
from umabot.skills import SkillRegistry
from umabot.storage import Database, Queue
from umabot.tasks.parser import parse_control_task_request
from umabot.tasks.schedule import compute_initial_next_run_at, compute_next_run_at
from umabot.tools import ToolRegistry, UnifiedToolRegistry
from umabot.tools.registry import Attachment, RISK_RED
from umabot.tools.builtin import set_active_skill_env
from umabot.tools.workspace import (
    detect_workspace_from_text,
    enforce_path,
    get_active_workspace,
    resolve_workspace,
    set_active_workspace,
)


logger = logging.getLogger("umabot.worker")


class Worker:
    def __init__(
        self,
        *,
        config,
        db: Database,
        queue: Queue,
        tool_registry: ToolRegistry,
        policy: PolicyEngine,
        skill_registry: SkillRegistry,
        unified_registry: UnifiedToolRegistry,
        send_message,
        send_control_message,
        send_confirmation_request=None,
        send_control_observability_event=None,
    ) -> None:
        self.config = config
        self.db = db
        self.queue = queue
        self.tool_registry = tool_registry
        self.policy = policy
        self.skill_registry = skill_registry
        self.unified_registry = unified_registry
        self.send_message = send_message
        self.send_control_message = send_control_message
        self.send_confirmation_request = send_confirmation_request
        self.send_control_observability_event = send_control_observability_event
        self._stop = asyncio.Event()
        self._tasks: List[asyncio.Task] = []
        self._chat_locks: Dict[str, asyncio.Lock] = {}
        self._pending_orchestrator_approvals: Dict[str, asyncio.Future] = {}
        self._context_state = ContextState(revision=0, fingerprint="", system_messages=[])

        # Build a shared token bucket when agents are enabled and a budget is configured.
        # The same bucket is shared across all LLM clients so the total token spend
        # per minute is capped — regardless of which client fires a request.
        agents_cfg = getattr(config, "agents", None)
        tpm = getattr(agents_cfg, "tokens_per_minute", 0) if agents_cfg else 0
        self._token_bucket: TokenBucket | None = TokenBucket(tpm) if tpm > 0 else None

        # Wrap each LLM client in a priority scheduler.
        # All existing callers default to P1; future callers can pass priority=P0/P2.
        self.llm_client = LLMScheduler(_build_llm_client(config, rate_limiter=self._token_bucket))

        # Security policy layer
        security_cfg = getattr(config, "security", None)
        self.security_policy = SecurityPolicy(security_cfg) if security_cfg else None
        self.declarative_policy = DeclarativePolicyEngine(
            getattr(getattr(config, "policy", None), "rules", [])
        )

        # Build separate LLM clients for the orchestration system if enabled.
        # Both inherit missing fields (provider, api_key) from the top-level llm config.
        if agents_cfg and agents_cfg.enabled:
            self.orchestrator_llm = LLMScheduler(
                _build_agent_llm_client(
                    config, agents_cfg.orchestrator, rate_limiter=self._token_bucket
                )
            )
            self.agent_llm = LLMScheduler(
                _build_agent_llm_client(
                    config, agents_cfg.worker, rate_limiter=self._token_bucket
                )
            )
        else:
            self.orchestrator_llm = None
            self.agent_llm = None

        # Load user-defined agent context from AGENT.md (empty string if file absent)
        self.agent_context = _load_agent_context(getattr(agents_cfg, "context_file", ""))
        self.team_registry = TeamRegistry(
            db=self.db,
            tool_registry=self.tool_registry,
            skill_registry=self.skill_registry,
            config=self.config,
        )

    @property
    def _concurrency(self) -> int:
        return max(1, getattr(getattr(self.config, "worker", None), "concurrency", 1))

    async def start(self) -> None:
        self._stop.clear()
        # Start LLM schedulers before spawning worker loops
        self.llm_client.start()
        if self.orchestrator_llm:
            self.orchestrator_llm.start()
        if self.agent_llm:
            self.agent_llm.start()
        self._tasks = [
            asyncio.create_task(self._worker_loop(), name=f"worker-{i}")
            for i in range(self._concurrency)
        ]
        logger.info("Worker started with concurrency=%d", self._concurrency)

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        # Stop LLM schedulers after worker loops exit
        await self.llm_client.stop()
        if self.orchestrator_llm:
            await self.orchestrator_llm.stop()
        if self.agent_llm:
            await self.agent_llm.stop()

    async def _notify(
        self,
        channel: str,
        chat_id: str,
        text: str,
        connector: str,
        *,
        connector_role: str = "admin",
        attachments: Optional[list] = None,
    ) -> None:
        """Send a reply to the right destination based on connector role.

        listener → broadcast to ALL admin panels via send_control_message.
        admin    → reply to the originating channel/connector only.
        """
        if connector_role == "listener":
            await self.send_control_message(channel, chat_id, text)
        else:
            await self.send_message(channel, chat_id, text, connector=connector, attachments=attachments)

    async def _worker_loop(self) -> None:
        """Single worker loop — multiple of these run concurrently."""
        while not self._stop.is_set():
            job = await self.queue.claim()
            if not job:
                await asyncio.sleep(0.2)
                continue
            # Clear any leftover skill env / workspace from the previous job.
            set_active_skill_env(None)
            set_active_workspace(None)
            try:
                await self._process_job(job)
                await self.queue.complete(job["id"])
            except Exception as exc:
                logger.exception("Worker job failed id=%s error=%s", job.get("id"), exc)
                await self.queue.fail(job["id"], str(exc))

    async def _process_job(self, job: Dict[str, Any]) -> None:
        payload = json.loads(job["payload_json"])
        job_type = payload.get("type", "message")
        logger.debug("Worker processing job id=%s type=%s", job.get("id"), job_type)
        if job_type == "confirm":
            await self._process_confirmation(payload)
            return
        if job_type == "agent_approve":
            await self.resolve_orchestrator_approval(
                str(payload.get("token", "")),
                bool(payload.get("approved", False)),
            )
            return
        if job_type == "task_run":
            await self._process_task_run(payload)
            return
        await self._process_message(payload)

    async def _process_message(self, payload: Dict[str, Any]) -> None:
        chat_id = payload["chat_id"]
        channel = payload["channel"]
        session_id = payload["session_id"]
        text = payload["text"]
        kind = payload.get("kind", "external")
        connector = payload.get("connector", "")
        connector_role = payload.get("connector_role", "admin")
        source_connector = payload.get("source_connector", connector)
        source_chat_id = payload.get("source_chat_id", chat_id)

        # Cross-connector reply routing: if a connector (e.g. gmail_imap) specifies
        # reply_* fields, send all outbound responses there instead of back to the origin.
        _rc = payload.get("reply_connector", "")
        _rid = payload.get("reply_chat_id", "")
        _rch = payload.get("reply_channel", "")
        if _rc and _rid and _rch:
            connector = _rc
            chat_id = _rid
            channel = _rch

        lock = self._chat_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            self.skill_registry.refresh()
            self.team_registry.refresh()
            self._context_state = _refresh_context_state(
                self._context_state, self.skill_registry, self.tool_registry
            )
            if kind == "control":
                handled = await self._handle_control_task_commands(
                    channel=channel,
                    chat_id=chat_id,
                    connector=connector,
                    text=text,
                )
                if handled:
                    return

            # Intent detection for listener connectors (gmail, telegram_user, …).
            # Runs a cheap P2 LLM call to classify the inbound message before the
            # full agent loop.  Low-importance "ignore" messages are discarded here.
            intent: Optional[IntentResult] = None
            if connector_role == "listener":
                intent = await detect_intent(text, self.llm_client)
                logger.info(
                    "Intent detected connector_role=listener importance=%s "
                    "needs_admin=%s action=%s",
                    intent.importance, intent.needs_admin, intent.suggested_action,
                )
                # Safety override: never silently drop inbound Gmail listener emails.
                # LLM intent classification can occasionally misclassify real emails
                # as low/ignore; for gmail_imap we always surface at least a summary.
                if "gmail" in (source_connector or "").lower() and intent.should_skip:
                    logger.info(
                        "Overriding gmail listener intent low/ignore -> summarize source_connector=%s",
                        source_connector,
                    )
                    intent.importance = "medium"
                    intent.needs_admin = True
                    intent.suggested_action = "summarize"
                    if not intent.summary:
                        intent.summary = "Inbound Gmail message received."
                if self.declarative_policy.has_rules:
                    intent_decision = self.declarative_policy.decide_intent(
                        RuleContext(
                            connector=connector,
                            source_connector=source_connector,
                            connector_role=connector_role,
                            channel=channel,
                            direction="inbound",
                            kind=kind,
                            action=intent.suggested_action,
                            importance=intent.importance,
                            needs_admin=intent.needs_admin,
                            admin_explicit=False,
                        )
                    )
                    if intent_decision.rule_id:
                        logger.info(
                            "Declarative policy intent rule matched id=%s reason=%s",
                            intent_decision.rule_id,
                            intent_decision.reason,
                        )
                    if intent_decision.ingest_to_llm is False:
                        logger.info(
                            "Dropping inbound message by declarative policy rule=%s connector=%s",
                            intent_decision.rule_id or "-",
                            source_connector or connector,
                        )
                        return
                    if intent_decision.set_importance:
                        intent.importance = intent_decision.set_importance
                    if intent_decision.set_action:
                        intent.suggested_action = intent_decision.set_action
                    if intent_decision.set_needs_admin is not None:
                        intent.needs_admin = intent_decision.set_needs_admin
                if intent.should_skip:
                    logger.info(
                        "Skipping low-importance ignore message from listener connector=%s",
                        connector,
                    )
                    return

            is_gmail_listener_flow = (
                connector_role == "listener" and "gmail" in (source_connector or "").lower()
            )
            explicit_admin_gmail_search = _is_explicit_admin_gmail_search_request(
                text=text,
                connector_role=connector_role,
                kind=kind,
            )

            # Listener messages are processed as one-shot prompts — no session
            # history is loaded.  Raw emails / channel messages are never stored
            # in the DB, so the admin session stays lean.  The LLM gets:
            #   [intent_context_block][date][current text]
            # Admin messages load the normal 20-message window.
            if connector_role == "listener":
                messages = [_date_system_message(), {"role": "user", "content": text}]
            else:
                # Persist the incoming user message so it appears in chat history
                # after a page reload (assistant messages are already stored on reply).
                self.db.add_message(session_id, "user", text)
                messages = self.db.list_recent_messages(session_id, limit=20)
                messages = [_date_system_message()] + messages

            # Build system messages: skill catalog + active skill instructions.
            # Also set the active skill's resolved env so shell.run and run_script
            # use the correct PATH / venv for this job.
            # Gmail listener summarize flow should work only from inbound payload.
            # Avoid skill-trigger amplification that may encourage follow-up Gmail API calls.
            if not is_gmail_listener_flow:
                system_messages = _build_skill_system_messages(self.skill_registry, user_text=text)
                if system_messages:
                    messages = system_messages + messages

            # Prepend intent context so the agent knows what action is expected
            # without re-reading the full message body.  Source connector/chat_id
            # are included so the LLM can route a reply to the right tool.
            if intent is not None:
                messages = [
                    {
                        "role": "system",
                        "content": intent_context_block(
                            intent,
                            source_connector=source_connector,
                            source_chat_id=source_chat_id,
                        ),
                    }
                ] + messages
            if is_gmail_listener_flow:
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "This is an inbound gmail_imap listener event.\n"
                            "You must summarize strictly from the provided email payload.\n"
                            "Do not ask the admin to paste raw email, message IDs, or run Gmail API fetches.\n"
                            "If payload is incomplete, say what fields are available and provide best-effort summary."
                        ),
                    }
                ] + messages
            matched_skill = None
            if not is_gmail_listener_flow:
                matched_skill = self.skill_registry.match_trigger(text)
                if matched_skill and matched_skill.resolved_runtime:
                    set_active_skill_env(matched_skill.resolved_runtime.env)

            # Resolve active workspace: detect from user message, else default
            configured_workspaces = getattr(
                getattr(self.config, "tools", None), "workspaces", []
            ) or []
            detected_ws = detect_workspace_from_text(text, configured_workspaces)
            active_ws = detected_ws or resolve_workspace("", configured_workspaces)
            set_active_workspace(active_ws)

            allowed_tools = self._allowed_tools()
            agents_cfg = getattr(self.config, "agents", None)
            using_orchestrator = bool(agents_cfg and agents_cfg.enabled and self.orchestrator_llm)

            # Listener-origin Gmail summaries must not trigger additional Gmail API fetches.
            # The inbound IMAP payload already contains the email body/headers needed to summarize.
            if is_gmail_listener_flow:
                if allowed_tools:
                    logger.info(
                        "Using tool-free mode for listener summarize flow source_connector=%s",
                        source_connector,
                    )
                allowed_tools = []

            # gmail.search should only be available when explicitly requested by an admin user.
            if "gmail.search" in allowed_tools and not explicit_admin_gmail_search:
                allowed_tools = [name for name in allowed_tools if name != "gmail.search"]
                logger.debug(
                    "gmail.search removed from allowed tools (requires explicit admin request)"
                )
            if self.declarative_policy.has_rules:
                allowed_tools = self.declarative_policy.filter_tools_for_context(
                    allowed_tools,
                    RuleContext(
                        connector=connector,
                        source_connector=source_connector,
                        connector_role=connector_role,
                        channel=channel,
                        direction="outbound",
                        kind=kind,
                        action=(intent.suggested_action if intent else ""),
                        importance=(intent.importance if intent else ""),
                        needs_admin=(intent.needs_admin if intent else None),
                        admin_explicit=explicit_admin_gmail_search,
                    ),
                    drop_confirm_required=using_orchestrator,
                )

            # Route through the dynamic orchestrator when it's enabled
            if using_orchestrator and not is_gmail_listener_flow:
                team_cfg = getattr(self.config, "agent_teams", None)
                team_feature_enabled = bool(getattr(team_cfg, "enabled", False))
                if team_feature_enabled:
                    fit = self.team_registry.fit_check(
                        text,
                        min_len=int(getattr(team_cfg, "fit_min_query_len", 60) or 60),
                        enabled=bool(getattr(team_cfg, "fit_gate_enabled", True)),
                    )
                    await self._emit_team_event(
                        "team.fit.started",
                        {
                            "passed": fit.get("passed"),
                            "reason": fit.get("reason"),
                            "complexity_class": fit.get("complexity_class"),
                            "task_len": len(text or ""),
                        },
                    )

                    if fit.get("passed"):
                        selection = self.team_registry.select_team(
                            task=text,
                            allowed_tools=allowed_tools,
                            default_threshold=float(
                                getattr(team_cfg, "default_confidence_threshold", 0.62) or 0.62
                            ),
                            max_teams_considered=int(
                                getattr(team_cfg, "max_teams_considered", 20) or 20
                            ),
                            routing_mode=str(getattr(team_cfg, "routing_mode", "hybrid") or "hybrid"),
                        )
                        if selection.team:
                            await self._emit_team_event(
                                "team.route.selected",
                                {
                                    "team_id": selection.team.get("id"),
                                    "team_name": selection.team.get("name"),
                                    "score": selection.score,
                                    "threshold": selection.threshold,
                                    "selected_by": selection.selected_by,
                                    "rationale": selection.rationale,
                                },
                            )
                            try:
                                final_reply, final_attachments = await self._run_with_team(
                                    team=selection.team,
                                    task=text,
                                    history=messages,
                                    channel=channel,
                                    chat_id=chat_id,
                                    connector=connector,
                                    session_id=session_id,
                                    allowed_tools=allowed_tools,
                                    agents_cfg=agents_cfg,
                                    complexity_class=str(fit.get("complexity_class", "moderate")),
                                    selected_by=selection.selected_by,
                                    route_rationale=selection.rationale,
                                    source_connector=source_connector,
                                    kind=kind,
                                    intent=intent,
                                    explicit_admin_gmail_search=explicit_admin_gmail_search,
                                    connector_role=connector_role,
                                )
                            except Exception as exc:
                                logger.exception("Team execution failed team_id=%s error=%s", selection.team.get("id"), exc)
                                await self._notify(
                                    channel,
                                    chat_id,
                                    f"Team execution failed: {exc}",
                                    connector,
                                    connector_role=connector_role,
                                )
                                return
                            if final_reply:
                                if not final_attachments:
                                    final_attachments = self._build_orchestrator_attachment_fallback(final_reply)
                                message_id = self.db.add_message(session_id, "assistant", final_reply)
                                if final_attachments:
                                    self.db.add_message_attachments(message_id, final_attachments)
                                await self._notify(
                                    channel,
                                    chat_id,
                                    final_reply,
                                    connector,
                                    connector_role=connector_role,
                                    attachments=final_attachments,
                                )
                            return

                        await self._emit_team_event(
                            "team.route.not_selected",
                            {
                                "score": selection.score,
                                "threshold": selection.threshold,
                                "selected_by": selection.selected_by,
                                "rationale": selection.rationale,
                            },
                        )
                        if not bool(getattr(team_cfg, "fallback_to_dynamic", True)):
                            await self._notify(
                                channel,
                                chat_id,
                                "No agent team matched this request and dynamic fallback is disabled.",
                                connector,
                                connector_role=connector_role,
                            )
                            return
                    else:
                        await self._emit_team_event(
                            "team.fit.rejected",
                            {
                                "reason": fit.get("reason"),
                                "complexity_class": fit.get("complexity_class"),
                            },
                        )
                        if not bool(getattr(team_cfg, "fallback_to_dynamic", True)):
                            await self._notify(
                                channel,
                                chat_id,
                                f"Multi-agent fit rejected: {fit.get('reason', 'unknown reason')}",
                                connector,
                                connector_role=connector_role,
                            )
                            return

                logger.info(
                    "Routing to DynamicOrchestrator kind=%s session_id=%s",
                    kind, session_id,
                )
                # Progressive disclosure: only tell the orchestrator WHICH skill
                # matched. The orchestrator (or its spawned agent) must call
                # skill.get_instructions(skill_name=...) to retrieve the full body.
                # This avoids dumping thousands of tokens into every LLM call upfront.
                skill_context = ""
                if matched_skill:
                    skill_context = (
                        f"ACTIVE SKILL: {matched_skill.metadata.name}\n"
                        f"Skill directory: {matched_skill.path}\n"
                        f"The user's request matches this skill. Use skill.get_instructions(skill_name='{matched_skill.metadata.name}') "
                        f"to load the full instructions before spawning the specialist agent."
                    )
                skill_context_full = ""  # No longer pre-loaded; fetched on demand
                try:
                    final_reply, final_attachments = await self._run_with_orchestrator(
                        task=text,
                        history=messages,
                        channel=channel,
                        chat_id=chat_id,
                        connector=connector,
                        session_id=session_id,
                        allowed_tools=allowed_tools,
                        agents_cfg=agents_cfg,
                        skill_context=skill_context,
                        skill_context_full=skill_context_full,
                        source_connector=source_connector,
                        kind=kind,
                        intent=intent,
                        explicit_admin_gmail_search=explicit_admin_gmail_search,
                        connector_role=connector_role,
                    )
                except Exception as exc:
                    logger.exception("Orchestrator failed kind=%s error=%s", kind, exc)
                    await self._notify(channel, chat_id, f"Orchestrator failed: {exc}", connector, connector_role=connector_role)
                    return
                if final_reply:
                    if not final_attachments:
                        final_attachments = self._build_orchestrator_attachment_fallback(final_reply)
                    message_id = self.db.add_message(session_id, "assistant", final_reply)
                    if final_attachments:
                        self.db.add_message_attachments(message_id, final_attachments)
                    await self._notify(
                        channel,
                        chat_id,
                        final_reply,
                        connector,
                        connector_role=connector_role,
                        attachments=final_attachments,
                    )
                return
            if using_orchestrator and is_gmail_listener_flow:
                logger.info(
                    "Bypassing DynamicOrchestrator for gmail listener summarize flow source_connector=%s",
                    source_connector,
                )

            tools_spec = _build_tool_specs(self.tool_registry, allowed_tools)

            logger.debug(
                "LLM request kind=%s session_id=%s messages=%s tools=%s ctx_rev=%s",
                kind,
                session_id,
                len(messages),
                len(tools_spec),
                self._context_state.revision,
            )
            try:
                response = await self.llm_client.generate(messages, tools=tools_spec)
            except Exception as exc:
                logger.exception("LLM request failed kind=%s error=%s", kind, exc)
                await self._notify(channel, chat_id, "LLM request failed. Check logs.", connector, connector_role=connector_role)
                return
            logger.debug(
                "LLM response content_len=%s tool_calls=%s",
                len(response.content or ""),
                len(response.tool_calls),
            )
            assistant_message_id = self.db.add_message(
                session_id, "assistant", response.content or ""
            )
            messages.append(_assistant_message(response))

            if not response.tool_calls:
                if response.content:
                    await self._notify(channel, chat_id, response.content, connector, connector_role=connector_role)
                return

            tool_results = []
            for tool_call in response.tool_calls:
                arg_count = len(tool_call.arguments) if isinstance(tool_call.arguments, dict) else 0
                logger.debug("Tool call requested name=%s arg_count=%s", tool_call.name, arg_count)

                # Hard policy guardrails in case a downstream path attempts these tools.
                if tool_call.name == "gmail.search" and not explicit_admin_gmail_search:
                    logger.info("Blocked gmail.search: explicit admin request required")
                    await self._notify(
                        channel,
                        chat_id,
                        "Tool denied: `gmail.search` is only allowed when explicitly requested by an admin user.",
                        connector,
                        connector_role=connector_role,
                    )
                    return
                if tool_call.name == "gmail.read" and is_gmail_listener_flow:
                    logger.info(
                        "Blocked gmail.read for listener summarize flow source_connector=%s",
                        source_connector,
                    )
                    await self._notify(
                        channel,
                        chat_id,
                        "Tool denied: `gmail.read` is disabled for inbound gmail_imap summarize flow.",
                        connector,
                        connector_role=connector_role,
                    )
                    return
                if is_gmail_listener_flow and (
                    tool_call.name.startswith("gmail.") or tool_call.name == "google.authorize"
                ):
                    logger.info(
                        "Blocked %s for listener summarize flow source_connector=%s",
                        tool_call.name,
                        source_connector,
                    )
                    await self._notify(
                        channel,
                        chat_id,
                        f"Tool denied: `{tool_call.name}` is disabled for inbound gmail_imap summarize flow.",
                        connector,
                        connector_role=connector_role,
                    )
                    return

                if self.declarative_policy.has_rules:
                    tool_decision = self.declarative_policy.decide_tool(
                        tool_call.name,
                        RuleContext(
                            connector=connector,
                            source_connector=source_connector,
                            connector_role=connector_role,
                            channel=channel,
                            direction="outbound",
                            kind=kind,
                            action=(intent.suggested_action if intent else ""),
                            importance=(intent.importance if intent else ""),
                            needs_admin=(intent.needs_admin if intent else None),
                            admin_explicit=explicit_admin_gmail_search,
                        ),
                    )
                    if tool_decision.effect == "deny":
                        reason = tool_decision.reason or "blocked by declarative policy"
                        logger.info(
                            "Declarative policy denied tool=%s rule=%s reason=%s",
                            tool_call.name,
                            tool_decision.rule_id,
                            reason,
                        )
                        await self._notify(
                            channel,
                            chat_id,
                            f"Tool denied by policy: {reason}",
                            connector,
                            connector_role=connector_role,
                        )
                        return
                    if tool_decision.effect == "require_confirmation":
                        if self._should_auto_approve_tool_confirmation(
                            tool_name=tool_call.name,
                            tool_arguments=tool_call.arguments,
                            allowed_tools=allowed_tools,
                        ):
                            logger.info(
                                "Auto-approved declarative confirmation tool=%s rule=%s workspace=%s",
                                tool_call.name,
                                tool_decision.rule_id,
                                self._active_workspace_name(),
                            )
                            self.db.add_audit(
                                "tool_auto_approved",
                                {
                                    "tool": tool_call.name,
                                    "source": "declarative_policy",
                                    "workspace": self._active_workspace_name(),
                                    "reason": tool_decision.reason or "",
                                },
                                chat_id=chat_id,
                                connector=connector,
                                decision="auto_approved",
                            )
                        else:
                            decision = self.policy.request_confirmation(
                                tool_call={
                                    "name": tool_call.name,
                                    "arguments": tool_call.arguments,
                                    "id": tool_call.id,
                                },
                                chat_id=chat_id,
                                channel=channel,
                                connector=connector,
                                session_id=session_id,
                                message_id=assistant_message_id,
                                messages=messages,
                                reason=tool_decision.reason or "confirmation required by declarative policy",
                            )
                            logger.info(
                                "Declarative policy requires confirmation tool=%s rule=%s",
                                tool_call.name,
                                tool_decision.rule_id,
                            )
                            await self._request_tool_confirmation(
                                channel=channel,
                                chat_id=chat_id,
                                connector=connector,
                                tool_call_name=tool_call.name,
                                tool_call_arguments=tool_call.arguments,
                                token=decision.token or "",
                            )
                            return

                # Security layer: evaluate before risk-level policy
                if self.security_policy:
                    sec = self.security_policy.evaluate(
                        tool_call.name,
                        user_id=chat_id,   # chat_id proxies user identity for single-user chats
                        connector=connector,
                    )
                    if not sec.allowed:
                        logger.info("Security deny tool=%s reason=%s", tool_call.name, sec.reason)
                        self.db.add_audit(
                            "tool_security_denied",
                            {"tool": tool_call.name, "reason": sec.reason},
                            chat_id=chat_id,
                            connector=connector,
                            decision="denied",
                        )
                        await self._notify(channel, chat_id, f"Access denied: {sec.reason}", connector, connector_role=connector_role)
                        return

                # SSRF protection: check url argument for tools that make HTTP requests
                if self.security_policy and getattr(getattr(self.config, "security", None), "ssrf_protection", True):
                    url_arg = (tool_call.arguments or {}).get("url", "") if isinstance(tool_call.arguments, dict) else ""
                    if url_arg:
                        try:
                            check_ssrf(url_arg)
                        except SSRFError as ssrf_exc:
                            logger.warning("SSRF blocked tool=%s url=%s: %s", tool_call.name, url_arg, ssrf_exc)
                            self.db.add_audit(
                                "ssrf_blocked",
                                {"tool": tool_call.name, "url": url_arg, "reason": str(ssrf_exc)},
                                chat_id=chat_id,
                                connector=connector,
                                decision="blocked",
                            )
                            await self._notify(channel, chat_id, f"Request blocked for security reasons: {ssrf_exc}", connector, connector_role=connector_role)
                            return

                decision = self.policy.evaluate(
                    {"name": tool_call.name, "arguments": tool_call.arguments, "id": tool_call.id},
                    allowed_tools,
                    chat_id=chat_id,
                    channel=channel,
                    connector=connector,
                    session_id=session_id,
                    message_id=assistant_message_id,
                    messages=messages,
                )
                if decision.require_confirmation:
                    if self._should_auto_approve_tool_confirmation(
                        tool_name=tool_call.name,
                        tool_arguments=tool_call.arguments,
                        allowed_tools=allowed_tools,
                    ):
                        logger.info(
                            "Auto-approved policy confirmation tool=%s workspace=%s",
                            tool_call.name,
                            self._active_workspace_name(),
                        )
                        self.db.add_audit(
                            "tool_auto_approved",
                            {
                                "tool": tool_call.name,
                                "source": "risk_policy",
                                "workspace": self._active_workspace_name(),
                            },
                            chat_id=chat_id,
                            connector=connector,
                            decision="auto_approved",
                        )
                    else:
                        logger.info("Tool confirmation required name=%s", tool_call.name)
                        await self._request_tool_confirmation(
                            channel=channel,
                            chat_id=chat_id,
                            connector=connector,
                            tool_call_name=tool_call.name,
                            tool_call_arguments=tool_call.arguments,
                            token=decision.token or "",
                        )
                        return
                if not decision.allowed:
                    logger.info("Tool denied name=%s reason=%s", tool_call.name, decision.reason)
                    await self._notify(channel, chat_id, f"Tool denied: {decision.reason}", connector, connector_role=connector_role)
                    return
                tool = self.tool_registry.get(tool_call.name)
                if not tool:
                    await self._notify(channel, chat_id, f"Tool not found: {tool_call.name}", connector, connector_role=connector_role)
                    return
                try:
                    result = await tool.handler(tool_call.arguments)
                except Exception as exc:
                    logger.exception("Tool execution failed name=%s", tool_call.name)
                    await self._notify(channel, chat_id, f"Tool `{tool_call.name}` failed: {exc}", connector, connector_role=connector_role)
                    return

                # Mask secrets in tool output before storing or returning to LLM
                masked_content = result.content
                if getattr(getattr(self.config, "security", None), "mask_secrets_in_output", True):
                    masked_content = mask_secrets(result.content or "")

                logger.debug("Tool result name=%s content_len=%s", tool_call.name, len(masked_content))
                self.db.add_tool_call(
                    assistant_message_id,
                    tool_call.name,
                    tool_call.arguments,
                    {"content": masked_content, "data": result.data},
                )
                self.db.add_audit(
                    "tool_executed",
                    {"tool": tool_call.name},
                    chat_id=chat_id,
                    connector=connector,
                    decision="allowed",
                )
                # Return masked content so secrets don't leak into LLM context
                from umabot.tools.registry import ToolResult as _TR
                tool_results.append((tool_call, _TR(content=masked_content, data=result.data, attachments=result.attachments)))

            # Collect all attachments produced by tool calls in this round
            pending_attachments = []
            for _, result in tool_results:
                pending_attachments.extend(result.attachments)

            for tool_call, result in tool_results:
                tool_message = {
                    "role": "tool",
                    "content": result.content,
                    "name": tool_call.name,
                }
                if tool_call.id:
                    tool_message["tool_call_id"] = tool_call.id
                messages.append(tool_message)

            follow_up = await self.llm_client.generate(messages, tools=None)
            if follow_up.content:
                serialized = [a.to_dict() for a in pending_attachments] if pending_attachments else None
                message_id = self.db.add_message(session_id, "assistant", follow_up.content)
                if serialized:
                    self.db.add_message_attachments(message_id, serialized)
                await self._notify(channel, chat_id, follow_up.content, connector, connector_role=connector_role, attachments=serialized)

    async def _run_with_orchestrator(
        self,
        *,
        task: str,
        history: List[Dict[str, Any]],
        channel: str,
        chat_id: str,
        connector: str,
        session_id: int,
        allowed_tools: List[str],
        agents_cfg,
        skill_context: str = "",
        skill_context_full: str = "",
        source_connector: str = "",
        kind: str = "external",
        intent: Optional[IntentResult] = None,
        explicit_admin_gmail_search: bool = False,
        connector_role: str = "admin",
    ) -> tuple[str, Optional[list]]:
        """Delegate to DynamicOrchestrator and return final reply + attachments."""
        configured_workspaces = getattr(
            getattr(self.config, "tools", None), "workspaces", []
        ) or []
        run_id = uuid.uuid4().hex[:12]
        self.db.create_agent_team_run(
            run_id=run_id,
            team_id=None,
            status="running",
            complexity_class="moderate",
            selected_by="rule",
            budget_snapshot={},
            route_rationale={"mode": "dynamic_orchestrator"},
        )

        async def send_update(message: str) -> None:
            await self._notify(channel, chat_id, f"[update] {message}", connector, connector_role=connector_role)

        async def send_observability_event(event_name: str, data: Dict[str, Any]) -> None:
            if not self.send_control_observability_event:
                return
            payload = dict(data or {})
            payload.setdefault("run_id", run_id)
            payload.setdefault("timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            logger.debug("obs_event db_write event=%s run_id=%s", event_name, run_id)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.db.add_agent_team_event(run_id=run_id, event_name=event_name, payload=payload),
            )
            logger.debug("obs_event panel_send event=%s run_id=%s", event_name, run_id)
            summary = _multi_agent_summary(event_name, payload)
            try:
                await self.send_control_observability_event(event_name, payload, summary)
            except Exception as exc:
                logger.debug("Failed to emit observability event %s: %s", event_name, exc)
            logger.debug("obs_event done event=%s run_id=%s", event_name, run_id)

        async def request_approval(reason: str, action_summary: str) -> bool:
            token = secrets.token_hex(8)
            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()
            self._pending_orchestrator_approvals[token] = future
            try:
                await send_observability_event(
                    "multi_agent_approval_requested",
                    {
                        "run_id": run_id,
                        "token": token,
                        "reason": reason,
                        "action_summary": action_summary,
                    },
                )
                return await asyncio.wait_for(asyncio.shield(future), timeout=300.0)
            except asyncio.TimeoutError:
                logger.warning("Orchestrator approval timed out token=%s", token)
                return False
            finally:
                self._pending_orchestrator_approvals.pop(token, None)

        agent_tool_guard = self._build_agent_tool_guard(
            allowed_tools=allowed_tools,
            chat_id=chat_id,
            channel=channel,
            connector=connector,
            source_connector=source_connector,
            connector_role=connector_role,
            kind=kind,
            action=(intent.suggested_action if intent else ""),
            importance=(intent.importance if intent else ""),
            needs_admin=(intent.needs_admin if intent else None),
            admin_explicit=explicit_admin_gmail_search,
        )

        orchestrator = DynamicOrchestrator(
            orchestrator_llm=self.orchestrator_llm,
            agent_llm=self.agent_llm,
            tool_registry=self.tool_registry,
            available_tool_names=allowed_tools,
            send_update_callback=send_update,
            request_approval_callback=request_approval,
            max_orchestrator_iterations=agents_cfg.max_orchestrator_iterations,
            max_agent_iterations=agents_cfg.max_agent_iterations,
            skill_context=skill_context,
            skill_context_full=skill_context_full,
            agent_context=self.agent_context,
            skill_registry=self.skill_registry,
            workspaces=configured_workspaces,
            run_id=run_id,
            on_event=send_observability_event,
            on_agent_tool_call=agent_tool_guard,
        )

        final_reply = await orchestrator.run(task=task, history=history)
        self.db.complete_agent_team_run(run_id=run_id, status="completed")
        serialized = [a.to_dict() for a in orchestrator.collected_attachments]
        return final_reply, (serialized or None)

    async def _run_with_team(
        self,
        *,
        team: Dict[str, Any],
        task: str,
        history: List[Dict[str, Any]],
        channel: str,
        chat_id: str,
        connector: str,
        session_id: int,
        allowed_tools: List[str],
        agents_cfg,
        complexity_class: str,
        selected_by: str,
        route_rationale: Dict[str, Any],
        source_connector: str = "",
        kind: str = "external",
        intent: Optional[IntentResult] = None,
        explicit_admin_gmail_search: bool = False,
        connector_role: str = "admin",
    ) -> tuple[str, Optional[list]]:
        configured_workspaces = getattr(
            getattr(self.config, "tools", None), "workspaces", []
        ) or []
        run_id = uuid.uuid4().hex[:12]

        async def send_update(message: str) -> None:
            await self._notify(
                channel,
                chat_id,
                f"[update] {message}",
                connector,
                connector_role=connector_role,
            )

        async def send_observability_event(event_name: str, data: Dict[str, Any]) -> None:
            payload = dict(data or {})
            payload.setdefault("run_id", run_id)
            payload.setdefault("timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            logger.debug("obs_event panel_send event=%s run_id=%s", event_name, run_id)
            summary = _multi_agent_summary(event_name, payload)
            if self.send_control_observability_event:
                try:
                    await self.send_control_observability_event(event_name, payload, summary)
                except Exception as exc:
                    logger.debug("Failed to emit observability event %s: %s", event_name, exc)
            logger.debug("obs_event done event=%s run_id=%s", event_name, run_id)

        agent_tool_guard = self._build_agent_tool_guard(
            allowed_tools=allowed_tools,
            chat_id=chat_id,
            channel=channel,
            connector=connector,
            source_connector=source_connector,
            connector_role=connector_role,
            kind=kind,
            action=(intent.suggested_action if intent else ""),
            importance=(intent.importance if intent else ""),
            needs_admin=(intent.needs_admin if intent else None),
            admin_explicit=explicit_admin_gmail_search,
        )

        executor = TeamExecutor(
            orchestrator_llm=self.orchestrator_llm,
            agent_llm=self.agent_llm,
            tool_registry=self.tool_registry,
            db=self.db,
            skill_registry=self.skill_registry,
            workspaces=configured_workspaces,
            agent_context=self.agent_context,
            on_event=send_observability_event,
            send_update=send_update,
            max_orchestrator_iterations=agents_cfg.max_orchestrator_iterations,
            max_agent_iterations=agents_cfg.max_agent_iterations,
            on_agent_tool_call=agent_tool_guard,
            request_approval=None,
            disable_approval=True,
        )

        final_reply, attachments = await executor.execute(
            team=team,
            task=task,
            history=history,
            run_id=run_id,
            complexity_class=complexity_class,
            selected_by=selected_by,
            route_rationale=route_rationale,
        )
        serialized = [a.to_dict() for a in attachments]
        return final_reply, (serialized or None)

    def _build_orchestrator_attachment_fallback(self, final_reply: str) -> Optional[list]:
        """Attach local image files referenced in text when tools returned none."""
        candidates = _extract_image_path_candidates(final_reply)
        if not candidates:
            return None

        roots: List[Path] = []
        active_ws = get_active_workspace()
        if active_ws:
            try:
                roots.append(Path(active_ws.path).expanduser().resolve())
            except Exception:
                pass

        for ws in (getattr(getattr(self.config, "tools", None), "workspaces", []) or []):
            try:
                roots.append(Path(ws.path).expanduser().resolve())
            except Exception:
                continue

        # Final fallback for relative paths in local development runs.
        roots.append(Path.cwd().resolve())

        attachments = _attachments_from_image_candidates(candidates, roots)
        if not attachments:
            return None
        logger.info(
            "Attachment fallback added %d image(s) from assistant text references",
            len(attachments),
        )
        return [a.to_dict() for a in attachments]

    async def _emit_team_event(self, event_name: str, payload: Dict[str, Any]) -> None:
        if not self.send_control_observability_event:
            return
        try:
            await self.send_control_observability_event(
                event_name,
                payload,
                _multi_agent_summary(event_name, payload),
            )
        except Exception as exc:
            logger.debug("Failed to emit team event %s: %s", event_name, exc)

    async def _process_task_run(self, payload: Dict[str, Any]) -> None:
        task_id = int(payload.get("task_id", 0))
        task = self.db.get_task(task_id)
        if not task:
            logger.warning("Task run requested for missing task_id=%s", task_id)
            return
        if task.get("status") != "active":
            self.db.release_task_lease(task_id)
            return

        run_id = self.db.create_task_run(task_id)
        chat_id = f"task:{task_id}"
        session_id = self.db.get_or_create_session(chat_id, "system", connector="scheduler")
        prompt = str(task.get("prompt", "")).strip() or "Run scheduled task."
        run_text = (
            f"Scheduled task '{task.get('name', '')}' is due now.\n"
            f"Task instruction: {prompt}\n"
            "Execute it and provide the final result."
        )
        self.db.add_message(session_id, "user", run_text)
        messages = self.db.list_recent_messages(session_id, limit=20)
        messages = [_date_system_message()] + messages
        self.skill_registry.refresh()
        self._context_state = _refresh_context_state(
            self._context_state, self.skill_registry, self.tool_registry
        )

        # Build system messages with skill matching on the task prompt.
        # Set the active skill env so tool calls in this task job use the right runtime.
        system_messages = _build_skill_system_messages(self.skill_registry, user_text=prompt)
        if system_messages:
            messages = system_messages + messages
        matched_skill = self.skill_registry.match_trigger(prompt)
        if matched_skill and matched_skill.resolved_runtime:
            set_active_skill_env(matched_skill.resolved_runtime.env)

        allowed_tools = self._allowed_tools()
        if self.declarative_policy.has_rules:
            allowed_tools = self.declarative_policy.filter_tools_for_context(
                allowed_tools,
                RuleContext(
                    connector="scheduler",
                    source_connector="scheduler",
                    connector_role="admin",
                    channel="system",
                    direction="outbound",
                    kind="task_run",
                    action="",
                    importance="",
                    needs_admin=True,
                    admin_explicit=True,
                ),
            )
        tools_spec = _build_tool_specs(self.tool_registry, allowed_tools)
        logger.debug(
            "Task run LLM request task_id=%s run_id=%s messages=%s tools=%s ctx_rev=%s",
            task_id,
            run_id,
            len(messages),
            len(tools_spec),
            self._context_state.revision,
        )
        try:
            response = await self.llm_client.generate(messages, tools=tools_spec)
            assistant_message_id = self.db.add_message(session_id, "assistant", response.content or "")
            messages.append(_assistant_message(response))
            final_content = response.content or ""
            if response.tool_calls:
                tool_results = []
                for tool_call in response.tool_calls:
                    if self.declarative_policy.has_rules:
                        tool_decision = self.declarative_policy.decide_tool(
                            tool_call.name,
                            RuleContext(
                                connector="scheduler",
                                source_connector="scheduler",
                                connector_role="admin",
                                channel="system",
                                direction="outbound",
                                kind="task_run",
                                action="",
                                importance="",
                                needs_admin=True,
                                admin_explicit=True,
                            ),
                        )
                        if tool_decision.effect == "deny":
                            text = (
                                f"Task #{task_id} denied tool '{tool_call.name}' by policy"
                                + (f": {tool_decision.reason}" if tool_decision.reason else ".")
                            )
                            self.db.fail_task_run(run_id=run_id, task_id=task_id, error=text)
                            await self.send_control_message("system", chat_id, text)
                            return
                        if tool_decision.effect == "require_confirmation":
                            if not self._should_auto_approve_tool_confirmation(
                                tool_name=tool_call.name,
                                tool_arguments=tool_call.arguments,
                                allowed_tools=allowed_tools,
                            ):
                                text = (
                                    f"Task #{task_id} requires confirmation for tool '{tool_call.name}' "
                                    "(declarative policy). Skipping run."
                                )
                                self.db.fail_task_run(run_id=run_id, task_id=task_id, error=text)
                                await self.send_control_message("system", chat_id, text)
                                return

                    decision = self.policy.evaluate(
                        {"name": tool_call.name, "arguments": tool_call.arguments},
                        allowed_tools,
                        chat_id=chat_id,
                        channel="system",
                        session_id=session_id,
                        message_id=assistant_message_id,
                        messages=messages,
                    )
                    if decision.require_confirmation:
                        if not self._should_auto_approve_tool_confirmation(
                            tool_name=tool_call.name,
                            tool_arguments=tool_call.arguments,
                            allowed_tools=allowed_tools,
                        ):
                            text = f"Task #{task_id} requires confirmation for tool '{tool_call.name}'. Skipping run."
                            self.db.fail_task_run(run_id=run_id, task_id=task_id, error=text)
                            await self.send_control_message("system", chat_id, text)
                            return
                    if not decision.allowed:
                        text = f"Task #{task_id} denied tool '{tool_call.name}': {decision.reason}"
                        self.db.fail_task_run(run_id=run_id, task_id=task_id, error=text)
                        await self.send_control_message("system", chat_id, text)
                        return
                    tool = self.tool_registry.get(tool_call.name)
                    if not tool:
                        text = f"Task #{task_id} missing tool '{tool_call.name}'"
                        self.db.fail_task_run(run_id=run_id, task_id=task_id, error=text)
                        await self.send_control_message("system", chat_id, text)
                        return
                    result = await tool.handler(tool_call.arguments)
                    self.db.add_tool_call(
                        assistant_message_id,
                        tool_call.name,
                        tool_call.arguments,
                        {"content": result.content, "data": result.data},
                    )
                    tool_results.append((tool_call, result))

                for tool_call, result in tool_results:
                    tool_message = {
                        "role": "tool",
                        "content": result.content,
                        "name": tool_call.name,
                    }
                    if tool_call.id:
                        tool_message["tool_call_id"] = tool_call.id
                    messages.append(tool_message)

                follow_up = await self.llm_client.generate(messages, tools=None)
                final_content = follow_up.content or final_content
                if follow_up.content:
                    self.db.add_message(session_id, "assistant", follow_up.content)

            next_run_at = compute_next_run_at(
                task["task_type"],
                task.get("schedule", {}),
                task.get("timezone") or "UTC",
            )
            is_terminal = task["task_type"] == "one_time"
            if not is_terminal and not next_run_at:
                raise RuntimeError("Unable to compute next run for periodic task")
            self.db.complete_task_run(
                run_id=run_id,
                task_id=task_id,
                result=final_content,
                next_run_at=None if is_terminal else next_run_at,
                terminal=is_terminal,
            )
            summary = f"Task #{task_id} ({task.get('name', '')}) completed.\n{final_content}"
            await self.send_control_message("system", chat_id, summary)
        except Exception as exc:
            self.db.fail_task_run(run_id=run_id, task_id=task_id, error=str(exc))
            await self.send_control_message("system", chat_id, f"Task #{task_id} failed: {exc}")

    async def _process_confirmation(self, payload: Dict[str, Any]) -> None:
        pending = payload["pending"]
        chat_id = pending["chat_id"]
        channel = pending["channel"]
        connector = pending.get("connector", "")
        session_id = pending["session_id"]
        message_id = pending["message_id"]
        tool_call = pending["tool_call"]
        messages = pending["messages"]

        tool = self.tool_registry.get(tool_call["name"])
        if not tool:
            await self.send_message(channel, chat_id, "Tool not found for confirmation.", connector=connector)
            return
        try:
            result = await tool.handler(tool_call["arguments"])
        except Exception as exc:
            logger.exception("Tool execution failed after confirmation name=%s", tool_call["name"])
            await self.send_message(
                channel, chat_id, f"Tool `{tool_call['name']}` failed to execute: {exc}", connector=connector
            )
            return
        self.db.add_tool_call(
            message_id,
            tool_call["name"],
            tool_call["arguments"],
            {"content": result.content, "data": result.data},
        )
        tool_message = {
            "role": "tool",
            "content": result.content,
            "tool_call_id": tool_call.get("id", ""),
        }
        messages.append(tool_message)
        follow_up = await self.llm_client.generate(messages, tools=None)
        if follow_up.content:
            self.db.add_message(session_id, "assistant", follow_up.content)
            await self.send_message(channel, chat_id, follow_up.content, connector=connector)

    async def resolve_orchestrator_approval(self, token: str, approved: bool) -> None:
        future = self._pending_orchestrator_approvals.get(token)
        if future and not future.done():
            future.set_result(approved)

    async def _handle_control_task_commands(
        self,
        *,
        channel: str,
        chat_id: str,
        connector: str,
        text: str,
    ) -> bool:
        stripped = (text or "").strip()
        lower = stripped.lower()
        if not stripped:
            return False

        if lower == "tasks list":
            tasks = self.db.list_tasks()
            if not tasks:
                await self.send_message(channel, chat_id, "No tasks found.", connector=connector)
                return True
            lines = []
            for task in tasks[:20]:
                lines.append(
                    f"#{task['id']} ({task['status']}) {task['name']} next={task.get('next_run_at') or '-'}"
                )
            await self.send_message(channel, chat_id, "\n".join(lines), connector=connector)
            return True

        if lower.startswith("tasks cancel "):
            task_id_raw = stripped.split(" ", 2)[-1].strip()
            try:
                task_id = int(task_id_raw)
            except ValueError:
                await self.send_message(channel, chat_id, "Usage: tasks cancel <id>", connector=connector)
                return True
            if self.db.cancel_task(task_id):
                await self.send_message(channel, chat_id, f"Task #{task_id} cancelled.", connector=connector)
            else:
                await self.send_message(channel, chat_id, f"Task #{task_id} not found.", connector=connector)
            return True

        draft = parse_control_task_request(stripped, timezone="UTC")
        if not draft:
            return False
        next_run_at = compute_initial_next_run_at(
            draft.task_type,
            draft.schedule,
            draft.timezone,
        )
        if not next_run_at:
            await self.send_message(
                channel,
                chat_id,
                "Could not parse task schedule. Use formats: "
                "'task daily HH:MM <prompt>', 'task weekly MON HH:MM <prompt>', or 'task once <ISO_DATETIME> <prompt>'.",
                connector=connector,
            )
            return True
        task_id = self.db.create_task(
            name=draft.name,
            prompt=draft.prompt,
            task_type=draft.task_type,
            schedule=draft.schedule,
            timezone=draft.timezone,
            next_run_at=next_run_at,
            created_by=f"control:{chat_id}",
        )
        self.db.add_audit(
            "task_created",
            {
                "task_id": task_id,
                "name": draft.name,
                "task_type": draft.task_type,
                "next_run_at": next_run_at,
            },
        )
        await self.send_message(
            channel,
            chat_id,
            f"Task created: #{task_id} '{draft.name}' ({draft.task_type}) next_run={next_run_at}",
            connector=connector,
        )
        return True

    async def _request_tool_confirmation(
        self,
        *,
        channel: str,
        chat_id: str,
        connector: str,
        tool_call_name: str,
        tool_call_arguments: Dict[str, Any],
        token: str,
    ) -> None:
        args_preview = json.dumps(tool_call_arguments, indent=2)[:400] if tool_call_arguments else ""
        if self.send_confirmation_request:
            await self.send_confirmation_request(
                channel,
                chat_id,
                connector,
                tool_call_name,
                args_preview,
                token,
            )
        else:
            prompt = (
                f"⚠️ Approval required: `{tool_call_name}`\n"
                f"Arguments:\n{args_preview}\n\n"
                f"Reply YES to approve or NO to deny."
            )
            await self.send_control_message(channel, chat_id, prompt)
        self.db.add_audit(
            "tool_confirmation_requested",
            {"tool": tool_call_name, "token": token},
            chat_id=chat_id,
            connector=connector,
            decision="pending_approval",
        )

    def _allowed_tools(self) -> list[str]:
        if self.policy.strictness == "strict":
            return []
        return list(self.tool_registry.list().keys())

    def _active_workspace_name(self) -> str:
        ws = get_active_workspace()
        if not ws:
            return ""
        return str(getattr(ws, "name", "") or "")

    def _build_agent_tool_guard(
        self,
        *,
        allowed_tools: List[str],
        chat_id: str,
        channel: str,
        connector: str,
        source_connector: str,
        connector_role: str,
        kind: str,
        action: str,
        importance: str,
        needs_admin: Optional[bool],
        admin_explicit: bool,
    ):
        """Build per-tool preflight guard for spawned/team agents.

        In auto_approve_workspace mode we fail closed for confirmation-required
        tool calls that are outside auto-approval policy, so agent teams do not
        stall on hidden approval requirements.
        """
        allowed_set = set(allowed_tools or [])

        async def _guard(tool_name: str, tool_arguments: Dict[str, Any]) -> None:
            policy_cfg = getattr(self.config, "policy", None)
            mode = str(getattr(policy_cfg, "approval_mode", "normal") or "normal").strip().lower()
            if mode != "auto_approve_workspace":
                return

            if tool_name not in allowed_set:
                raise RuntimeError(f"Tool not allowed in this run: {tool_name}")

            tool = self.tool_registry.get(tool_name)
            if not tool:
                raise RuntimeError(f"Unknown tool: {tool_name}")

            args = tool_arguments if isinstance(tool_arguments, dict) else {}
            try:
                self.tool_registry.validate_args(tool_name, args)
            except Exception as exc:
                raise RuntimeError(f"Invalid args for {tool_name}: {exc}") from exc

            if self.declarative_policy.has_rules:
                decision = self.declarative_policy.decide_tool(
                    tool_name,
                    RuleContext(
                        connector=connector,
                        source_connector=source_connector,
                        connector_role=connector_role,
                        channel=channel,
                        direction="outbound",
                        kind=kind,
                        action=action,
                        importance=importance,
                        needs_admin=needs_admin,
                        admin_explicit=admin_explicit,
                    ),
                )
                if decision.effect == "deny":
                    raise RuntimeError(
                        f"Tool denied by declarative policy: {decision.reason or tool_name}"
                    )
                if decision.effect == "require_confirmation":
                    if self._should_auto_approve_tool_confirmation(
                        tool_name=tool_name,
                        tool_arguments=args,
                        allowed_tools=allowed_tools,
                    ):
                        self.db.add_audit(
                            "tool_auto_approved",
                            {
                                "tool": tool_name,
                                "source": "agent_declarative_policy",
                                "workspace": self._active_workspace_name(),
                                "reason": decision.reason or "",
                            },
                            chat_id=chat_id,
                            connector=connector,
                            decision="auto_approved",
                        )
                    else:
                        raise RuntimeError(
                            "Approval required by declarative policy and auto_approve_workspace "
                            "did not match this tool call."
                        )

            if self.security_policy:
                sec = self.security_policy.evaluate(tool_name, user_id=chat_id, connector=connector)
                if not sec.allowed:
                    raise RuntimeError(f"Access denied: {sec.reason}")

                if getattr(getattr(self.config, "security", None), "ssrf_protection", True):
                    url_arg = str(args.get("url", "") or "").strip()
                    if url_arg:
                        check_ssrf(url_arg)

            if tool.risk_level == RISK_RED:
                if self._should_auto_approve_tool_confirmation(
                    tool_name=tool_name,
                    tool_arguments=args,
                    allowed_tools=allowed_tools,
                ):
                    self.db.add_audit(
                        "tool_auto_approved",
                        {
                            "tool": tool_name,
                            "source": "agent_risk_policy",
                            "workspace": self._active_workspace_name(),
                        },
                        chat_id=chat_id,
                        connector=connector,
                        decision="auto_approved",
                    )
                    return
                raise RuntimeError(
                    "Approval required for high-risk tool and auto_approve_workspace "
                    "did not match this tool call."
                )

        return _guard

    def _should_auto_approve_tool_confirmation(
        self,
        *,
        tool_name: str,
        tool_arguments: Dict[str, Any] | None,
        allowed_tools: List[str],
    ) -> bool:
        policy_cfg = getattr(self.config, "policy", None)
        mode = str(getattr(policy_cfg, "approval_mode", "normal") or "normal").strip().lower()
        if mode != "auto_approve_workspace":
            return False

        ws = get_active_workspace()
        if not ws:
            return False

        workspace_name = str(getattr(ws, "name", "") or "").strip().lower()
        allowed_workspaces = [
            str(item).strip().lower()
            for item in list(getattr(policy_cfg, "auto_approve_workspaces", []) or [])
            if str(item).strip()
        ]
        if allowed_workspaces and workspace_name not in allowed_workspaces:
            return False

        if tool_name not in allowed_tools:
            return False

        tool_patterns = [
            str(item).strip().lower()
            for item in list(getattr(policy_cfg, "auto_approve_tools", []) or [])
            if str(item).strip()
        ]
        if not tool_patterns:
            return False
        lowered_tool = str(tool_name or "").strip().lower()
        if not any(fnmatch.fnmatch(lowered_tool, pattern) for pattern in tool_patterns):
            return False

        args = tool_arguments if isinstance(tool_arguments, dict) else {}
        if tool_name == "shell.run":
            cmd = str(args.get("cmd", "") or "").strip()
            return self._is_auto_approve_shell_command_allowed(cmd)

        file_op_map = {
            "file.read": "read",
            "file.list": "list",
            "file.write": "write",
            "file.delete": "delete",
        }
        if tool_name in file_op_map:
            path_value = str(args.get("path", "") or "").strip()
            if not path_value:
                return False
            try:
                enforce_path(path_value, ws, operation=file_op_map[tool_name])
            except Exception:
                return False

        return True

    def _is_auto_approve_shell_command_allowed(self, cmd: str) -> bool:
        cmd = (cmd or "").strip()
        if not cmd:
            return False

        # Refuse command chains/subshells/redirection in auto-approve mode.
        forbidden_tokens = ("&&", "||", "|", ";", "$(", "`", ">", "<", "\n", "\r")
        if any(token in cmd for token in forbidden_tokens):
            return False

        policy_cfg = getattr(self.config, "policy", None)
        prefixes = [
            " ".join(str(item).strip().split())
            for item in list(getattr(policy_cfg, "auto_approve_shell_commands", []) or [])
            if str(item).strip()
        ]
        if not prefixes:
            return False

        normalized = " ".join(cmd.split())
        for prefix in prefixes:
            if normalized == prefix or normalized.startswith(prefix + " "):
                return True
        return False


def _build_agent_llm_client(config, agent_model_cfg, rate_limiter=None):
    """Build an LLM client for a specific agent role config.

    Fields left empty/None in ``agent_model_cfg`` inherit from ``config.llm``.
    The optional ``rate_limiter`` (TokenBucket) is shared across all clients.
    """
    provider = agent_model_cfg.provider or config.llm.provider
    model = agent_model_cfg.model or config.llm.model
    api_key = agent_model_cfg.api_key or _resolve_provider_api_key(config, provider)
    reasoning_effort = agent_model_cfg.reasoning_effort

    _validate_provider_config(config, provider, model, api_key, for_agent=True)

    provider = provider.lower()
    if provider == "openai":
        return OpenAIClient(api_key, model, reasoning_effort=reasoning_effort, rate_limiter=rate_limiter)
    if provider == "claude":
        return ClaudeClient(api_key, model, rate_limiter=rate_limiter)
    if provider == "gemini":
        return GeminiClient(api_key, model, rate_limiter=rate_limiter)
    raise RuntimeError(f"Unknown LLM provider for agent: {provider}")


_RE_GMAIL_SEARCH_PHRASE = re.compile(r"\bsearch\b.*\bgmail\b|\bgmail\b.*\bsearch\b")


def _is_explicit_admin_gmail_search_request(
    *,
    text: str,
    connector_role: str,
    kind: str,
) -> bool:
    """True when admin explicitly asks to run Gmail search.

    "Explicit" means either:
      - direct tool mention: gmail.search
      - clear natural-language phrase containing both "search" and "gmail"
    """
    if connector_role != "admin":
        return False
    # Guard against system-generated listener tasks being treated as explicit.
    if kind not in {"control", "external"}:
        return False
    lower = (text or "").lower()
    if "gmail.search" in lower:
        return True
    return bool(_RE_GMAIL_SEARCH_PHRASE.search(lower))


_RE_IMAGE_PATH_CANDIDATE = re.compile(
    r"[`'\"]?([^\s`'\"<>]+?\.(?:png|jpe?g|webp|gif|bmp))[`'\"]?",
    re.IGNORECASE,
)
_MAX_FALLBACK_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MiB


def _extract_image_path_candidates(text: str) -> List[str]:
    """Extract image-like path tokens from assistant text."""
    out: List[str] = []
    for m in _RE_IMAGE_PATH_CANDIDATE.finditer(text or ""):
        raw = (m.group(1) or "").strip().strip("()[]{}<>,;:")
        if not raw:
            continue
        lower = raw.lower()
        if lower.startswith(("http://", "https://", "data:")):
            continue
        out.append(raw)
    # Keep order, de-duplicate
    deduped: List[str] = []
    seen = set()
    for item in out:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _attachments_from_image_candidates(candidates: List[str], roots: List[Path]) -> List[Attachment]:
    """Resolve image candidates against roots and load them as attachments."""
    attachments: List[Attachment] = []
    seen_paths: set[str] = set()

    unique_roots: List[Path] = []
    for r in roots:
        key = str(r)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        unique_roots.append(r)
    seen_paths.clear()

    for raw in candidates:
        token = raw.strip().strip("`'\"")
        if not token:
            continue

        p = Path(token).expanduser()
        probes: List[Path] = []
        if p.is_absolute():
            probes.append(p)
        else:
            for root in unique_roots:
                probes.append(root / p)
                if p.parent == Path("."):
                    # Common model output: bare filename; look one level deep.
                    probes.extend(root.glob(f"**/{p.name}"))

        for probe in probes:
            try:
                resolved = probe.resolve()
            except Exception:
                continue
            key = str(resolved)
            if key in seen_paths:
                continue
            seen_paths.add(key)

            if not resolved.exists() or not resolved.is_file():
                continue
            size = resolved.stat().st_size
            if size <= 0 or size > _MAX_FALLBACK_IMAGE_BYTES:
                continue

            suffix = resolved.suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
                continue

            mime_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
            try:
                raw_bytes = resolved.read_bytes()
            except Exception:
                continue

            attachments.append(
                Attachment(
                    filename=resolved.name,
                    mime_type=mime_type,
                    data=raw_bytes,
                )
            )
            break

    return attachments


def _load_agent_context(context_file: str) -> str:
    """Read the user's AGENT.md file and return its contents.

    Returns an empty string silently if the file doesn't exist — the file is
    optional and its absence is the expected state for new installs.
    """
    if not context_file:
        return ""
    path = Path(context_file).expanduser()
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8").strip()
        if content:
            logger.info("Loaded agent context from %s (%d chars)", path, len(content))
        return content
    except OSError as exc:
        logger.warning("Could not read agent context file %s: %s", path, exc)
        return ""


def _build_llm_client(config, rate_limiter=None):
    provider = config.llm.provider.lower()
    model = config.llm.model
    api_key = config.llm.api_key
    if not api_key:
        api_key = _resolve_provider_api_key(config, provider)
    _validate_provider_config(config, provider, model, api_key, for_agent=False)
    if provider == "openai":
        return OpenAIClient(api_key, model, reasoning_effort=config.llm.reasoning_effort, rate_limiter=rate_limiter)
    if provider == "claude":
        return ClaudeClient(api_key, model, rate_limiter=rate_limiter)
    if provider == "gemini":
        return GeminiClient(api_key, model, rate_limiter=rate_limiter)
    raise RuntimeError(f"Unknown LLM provider: {provider}")


def _resolve_provider_api_key(config, provider: str) -> Optional[str]:
    provider = (provider or "").lower()
    providers = getattr(config, "llm_providers", {}) or {}
    provider_cfg = providers.get(provider) if isinstance(providers, dict) else None
    if provider_cfg is not None:
        api_key = getattr(provider_cfg, "api_key", None)
        if api_key:
            return api_key
    return getattr(getattr(config, "llm", None), "api_key", None)


def _validate_provider_config(
    config,
    provider: str,
    model: str,
    api_key: Optional[str],
    *,
    for_agent: bool,
) -> None:
    providers = getattr(config, "llm_providers", {}) or {}
    provider = (provider or "").lower()
    provider_cfg = providers.get(provider) if isinstance(providers, dict) else None

    if provider_cfg is not None:
        if not getattr(provider_cfg, "enabled", True):
            subject = "Agent LLM provider" if for_agent else "LLM provider"
            raise RuntimeError(f"{subject} '{provider}' is disabled in llm_providers")
        configured_models = [m for m in (getattr(provider_cfg, "models", []) or []) if m]
        if configured_models and model not in configured_models:
            raise RuntimeError(
                f"Model '{model}' is not enabled for provider '{provider}'. "
                f"Allowed models: {', '.join(configured_models)}"
            )

    if not api_key:
        if for_agent:
            raise RuntimeError(f"Agent LLM API key not configured for provider '{provider}'")
        raise RuntimeError(f"LLM API key not configured for provider '{provider}'")


def _build_tool_specs(
    registry: ToolRegistry,
    allowed_tools: list[str],
) -> List[Dict[str, Any]]:
    specs = []
    for tool in registry.list().values():
        if tool.name not in allowed_tools:
            continue
        specs.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.schema,
            }
        )
    return specs


def _assistant_message(response) -> Dict[str, Any]:
    message = {"role": "assistant", "content": response.content or ""}
    if response.tool_calls:
        message["tool_calls"] = [_tool_call_payload(call) for call in response.tool_calls if call.id]
    return message


def _tool_call_payload(call) -> Dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
    }


def _multi_agent_summary(event_name: str, payload: Dict[str, Any]) -> str:
    run_id = str(payload.get("run_id", ""))
    if event_name == "team.fit.started":
        passed = bool(payload.get("passed"))
        reason = str(payload.get("reason", ""))
        return f"[team-fit] {'accepted' if passed else 'rejected'}: {reason}"
    if event_name == "team.fit.rejected":
        reason = str(payload.get("reason", ""))
        return f"[team-fit] rejected: {reason}"
    if event_name == "team.route.selected":
        name = str(payload.get("team_name", "")) or f"team:{payload.get('team_id', '')}"
        score = payload.get("score")
        threshold = payload.get("threshold")
        return f"[team-route] selected `{name}` score={score} threshold={threshold}"
    if event_name == "team.route.not_selected":
        score = payload.get("score")
        threshold = payload.get("threshold")
        return f"[team-route] no team selected score={score} threshold={threshold}"
    if event_name == "multi_agent_run_started":
        task = str(payload.get("task", "") or "")
        if len(task) > 120:
            task = task[:117] + "..."
        return f"[multi-agent] Run `{run_id}` started. Task: {task}"
    if event_name == "multi_agent_node_added":
        role = str(payload.get("role", "agent"))
        node_id = str(payload.get("node_id", ""))
        return f"[multi-agent] Spawned `{role}` ({node_id})."
    if event_name == "multi_agent_node_status":
        role = str(payload.get("role", "agent"))
        status = str(payload.get("status", "unknown"))
        node_id = str(payload.get("node_id", ""))
        return f"[multi-agent] `{role}` ({node_id}) → {status}."
    if event_name == "multi_agent_run_completed":
        status = str(payload.get("status", "completed"))
        return f"[multi-agent] Run `{run_id}` {status}."
    if event_name == "multi_agent_node_log":
        role = str(payload.get("role", "agent"))
        msg = str(payload.get("message", "") or "").strip()
        if len(msg) > 120:
            msg = msg[:117] + "..."
        return f"[multi-agent] {role}: {msg}"
    return ""


@dataclass
class ContextState:
    revision: int
    fingerprint: str
    system_messages: List[Dict[str, str]]


def _refresh_context_state(
    previous: ContextState,
    skill_registry: SkillRegistry,
    tool_registry: ToolRegistry,
) -> ContextState:
    fingerprint = _context_fingerprint(skill_registry, tool_registry)
    if fingerprint == previous.fingerprint:
        return previous
    system_messages = _build_skill_system_messages(skill_registry)
    return ContextState(
        revision=previous.revision + 1,
        fingerprint=fingerprint,
        system_messages=system_messages,
    )


def _context_fingerprint(skill_registry: SkillRegistry, tool_registry: ToolRegistry) -> str:
    tools = sorted(tool_registry.list().keys())
    skills_repr = []
    for skill in sorted(skill_registry.list(), key=lambda s: s.metadata.name):
        skills_repr.append({"name": skill.metadata.name, "desc": skill.metadata.description})
    raw = json.dumps({"tools": tools, "skills": skills_repr}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_SKILL_SUMMARY_CHARS = 1500  # stage-2 preview cap


def _skill_summary(body: str, skill_name: str) -> str:
    """Return a condensed stage-2 summary of a SKILL.md body.

    Includes content up to the second '## ' section header (typically the
    overview + trigger/quick-start) capped at _SKILL_SUMMARY_CHARS.  The LLM
    is told it can call skill.get_instructions() for the full body.
    """
    hint = (
        f"\n\n[Summary only — call skill.get_instructions(skill_name='{skill_name}') "
        f"if you need the full instructions.]"
    )
    if len(body) <= _SKILL_SUMMARY_CHARS:
        return body  # short enough — no truncation needed

    # Find the second '## ' heading and cut there
    second_header_pos = -1
    headers_seen = 0
    for i, line_start in enumerate(_line_starts(body)):
        if body[line_start:line_start + 3] == "## ":
            headers_seen += 1
            if headers_seen == 2:
                second_header_pos = line_start
                break

    if 0 < second_header_pos <= _SKILL_SUMMARY_CHARS:
        return body[:second_header_pos].rstrip() + hint

    return body[:_SKILL_SUMMARY_CHARS].rstrip() + hint


def _line_starts(text: str):
    """Yield the character index of the start of each line."""
    yield 0
    for i, ch in enumerate(text):
        if ch == "\n" and i + 1 < len(text):
            yield i + 1


def _date_system_message() -> Dict[str, str]:
    """Return a system message containing the real current date and week range.

    Injected at the top of every LLM call so the model can resolve relative
    date references (today, this week, tomorrow, next Monday, etc.) correctly
    instead of guessing from its training-data cutoff.
    """
    from datetime import datetime, timedelta, timezone as _tz
    now = datetime.now(_tz.utc)
    week_start = now.date() - timedelta(days=now.weekday())   # Monday
    week_end   = week_start + timedelta(days=6)               # Sunday
    content = (
        f"Current date/time: {now.strftime('%Y-%m-%dT%H:%M:%SZ')} (UTC)\n"
        f"Today: {now.strftime('%A, %Y-%m-%d')}\n"
        f"This week: {week_start.isoformat()} (Mon) to {week_end.isoformat()} (Sun)\n"
        "When the user says 'today', 'tomorrow', 'this week', 'next Monday', etc., "
        "resolve them using the date above and pass concrete ISO 8601 timestamps to any tools."
    )
    return {"role": "system", "content": content}


def _build_skill_system_messages(
    skill_registry: SkillRegistry,
    user_text: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Build system messages for skill context using progressive disclosure.

    Stage 1 — catalog (always): all skill names + one-line descriptions.
    Stage 2 — summary (on trigger match): condensed SKILL.md overview.
    Stage 3 — full body (on demand): LLM calls skill.get_instructions() tool.
    """
    skills = skill_registry.list()
    if not skills:
        logger.debug("No skills loaded in registry")
        return []

    if user_text:
        logger.debug("Skill catalog: %d skills loaded", len(skills))
    messages: List[Dict[str, str]] = []

    # Stage 1 — skill catalog, always present
    catalog_lines = [
        "Available skills (activate by matching user intent):",
    ]
    for skill in sorted(skills, key=lambda s: s.metadata.name):
        catalog_lines.append(f"- {skill.metadata.name}: {skill.metadata.description}")
    catalog_lines.append(
        "\nTo get the full instructions for any skill, call skill.get_instructions(skill_name=...)."
    )
    messages.append({"role": "system", "content": "\n".join(catalog_lines)})

    # Stage 2 — condensed summary injected when user intent matches a skill
    if user_text:
        matched = skill_registry.match_trigger(user_text)
        if matched and matched.metadata.body:
            logger.debug("Skill matched: %s for text: %.80s", matched.metadata.name, user_text)
            summary = _skill_summary(matched.metadata.body, matched.metadata.name)
            instructions = (
                f"ACTIVE SKILL: {matched.metadata.name}\n"
                f"Skill directory: {matched.path}\n"
                f"Follow these instructions to complete the task:\n\n"
                f"{summary}"
            )
            messages.append({"role": "system", "content": instructions})
        else:
            logger.debug("No skill matched for text: %.80s", user_text)

    return messages
