from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from umabot.agents import DynamicOrchestrator
from umabot.llm import ClaudeClient, GeminiClient, OpenAIClient
from umabot.llm.rate_limiter import TokenBucket
from umabot.policy import PolicyEngine
from umabot.security import SecurityPolicy, mask_secrets
from umabot.security.ssrf import check_ssrf, SSRFError
from umabot.skills import SkillRegistry
from umabot.storage import Database, Queue
from umabot.tasks.parser import parse_control_task_request
from umabot.tasks.schedule import compute_initial_next_run_at, compute_next_run_at
from umabot.tools import ToolRegistry, UnifiedToolRegistry
from umabot.tools.builtin import set_active_skill_env


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
        self._stop = asyncio.Event()
        self._tasks: List[asyncio.Task] = []
        self._chat_locks: Dict[str, asyncio.Lock] = {}
        self._context_state = ContextState(revision=0, fingerprint="", system_messages=[])

        # Build a shared token bucket when agents are enabled and a budget is configured.
        # The same bucket is shared across all LLM clients so the total token spend
        # per minute is capped — regardless of which client fires a request.
        agents_cfg = getattr(config, "agents", None)
        tpm = getattr(agents_cfg, "tokens_per_minute", 0) if agents_cfg else 0
        self._token_bucket: TokenBucket | None = TokenBucket(tpm) if tpm > 0 else None

        self.llm_client = _build_llm_client(config, rate_limiter=self._token_bucket)

        # Security policy layer
        security_cfg = getattr(config, "security", None)
        self.security_policy = SecurityPolicy(security_cfg) if security_cfg else None

        # Build separate LLM clients for the orchestration system if enabled.
        # Both inherit missing fields (provider, api_key) from the top-level llm config.
        if agents_cfg and agents_cfg.enabled:
            self.orchestrator_llm = _build_agent_llm_client(
                config, agents_cfg.orchestrator, rate_limiter=self._token_bucket
            )
            self.agent_llm = _build_agent_llm_client(
                config, agents_cfg.worker, rate_limiter=self._token_bucket
            )
        else:
            self.orchestrator_llm = None
            self.agent_llm = None

        # Load user-defined agent context from AGENT.md (empty string if file absent)
        self.agent_context = _load_agent_context(getattr(agents_cfg, "context_file", ""))

    @property
    def _concurrency(self) -> int:
        return max(1, getattr(getattr(self.config, "worker", None), "concurrency", 1))

    async def start(self) -> None:
        self._stop.clear()
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

    async def _worker_loop(self) -> None:
        """Single worker loop — multiple of these run concurrently."""
        while not self._stop.is_set():
            job = await self.queue.claim()
            if not job:
                await asyncio.sleep(0.2)
                continue
            # Clear any leftover skill env from the previous job in this loop.
            set_active_skill_env(None)
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

        lock = self._chat_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            self.skill_registry.refresh()
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

            messages = self.db.list_recent_messages(session_id, limit=20)

            # Always prepend the current date so the LLM can resolve relative
            # date references ("today", "this week", "tomorrow", etc.) correctly.
            messages = [_date_system_message()] + messages

            # Build system messages: skill catalog + active skill instructions.
            # Also set the active skill's resolved env so shell.run and run_script
            # use the correct PATH / venv for this job.
            system_messages = _build_skill_system_messages(self.skill_registry, user_text=text)
            if system_messages:
                messages = system_messages + messages
            matched_skill = self.skill_registry.match_trigger(text)
            if matched_skill and matched_skill.resolved_runtime:
                set_active_skill_env(matched_skill.resolved_runtime.env)

            allowed_tools = self._allowed_tools()

            # Route through the dynamic orchestrator when it's enabled
            agents_cfg = getattr(self.config, "agents", None)
            if agents_cfg and agents_cfg.enabled and self.orchestrator_llm:
                logger.info(
                    "Routing to DynamicOrchestrator kind=%s session_id=%s",
                    kind, session_id,
                )
                # Pass the active skill's instructions to the orchestrator so it
                # can include them in the spawned agent's system_prompt.
                # We pass only the first 3000 chars to the orchestrator system prompt
                # (to stay within rate limits); the full body goes to the agent context.
                skill_context = ""
                if matched_skill and matched_skill.metadata.body:
                    body = matched_skill.metadata.body
                    skill_context = (
                        f"ACTIVE SKILL: {matched_skill.metadata.name}\n"
                        f"Skill directory: {matched_skill.path}\n"
                        f"Instructions (give the FULL text below to the relevant spawned agent's system_prompt and context):\n"
                        f"{body[:3000]}"
                        + ("... [truncated — pass full instructions to agent context]" if len(body) > 3000 else "")
                    )
                    # Store full body so orchestrator can include it in agent context
                    # by referencing skill_context_full
                skill_context_full = ""
                if matched_skill and matched_skill.metadata.body:
                    skill_context_full = (
                        f"ACTIVE SKILL: {matched_skill.metadata.name}\n"
                        f"Skill directory: {matched_skill.path}\n"
                        f"{matched_skill.metadata.body}"
                    )
                try:
                    final_reply = await self._run_with_orchestrator(
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
                    )
                except Exception as exc:
                    logger.exception("Orchestrator failed kind=%s error=%s", kind, exc)
                    await self.send_message(channel, chat_id, f"Orchestrator failed: {exc}", connector=connector)
                    return
                if final_reply:
                    self.db.add_message(session_id, "assistant", final_reply)
                    await self.send_message(channel, chat_id, final_reply, connector=connector)
                return

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
                await self.send_message(channel, chat_id, "LLM request failed. Check logs.")
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
                    await self.send_message(channel, chat_id, response.content, connector=connector)
                return

            tool_results = []
            for tool_call in response.tool_calls:
                arg_count = len(tool_call.arguments) if isinstance(tool_call.arguments, dict) else 0
                logger.debug("Tool call requested name=%s arg_count=%s", tool_call.name, arg_count)

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
                        await self.send_message(
                            channel, chat_id,
                            f"Access denied: {sec.reason}",
                            connector=connector,
                        )
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
                            await self.send_message(
                                channel, chat_id,
                                f"Request blocked for security reasons: {ssrf_exc}",
                                connector=connector,
                            )
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
                    logger.info("Tool confirmation required name=%s", tool_call.name)
                    args_preview = json.dumps(tool_call.arguments, indent=2)[:400] if tool_call.arguments else ""
                    if self.send_confirmation_request:
                        # Connector-native buttons (e.g. Telegram inline keyboard)
                        await self.send_confirmation_request(
                            channel, chat_id, connector,
                            tool_call.name, args_preview, decision.token,
                        )
                    else:
                        # Plain-text fallback — plain YES works via most-recent pending
                        prompt = (
                            f"⚠️ Approval required: `{tool_call.name}`\n"
                            f"Arguments:\n{args_preview}\n\n"
                            f"Reply YES to approve or NO to deny."
                        )
                        await self.send_control_message(channel, chat_id, prompt)
                    self.db.add_audit(
                        "tool_confirmation_requested",
                        {"tool": tool_call.name, "token": decision.token},
                        chat_id=chat_id,
                        connector=connector,
                        decision="pending_approval",
                    )
                    return
                if not decision.allowed:
                    logger.info("Tool denied name=%s reason=%s", tool_call.name, decision.reason)
                    await self.send_message(channel, chat_id, f"Tool denied: {decision.reason}", connector=connector)
                    return
                tool = self.tool_registry.get(tool_call.name)
                if not tool:
                    await self.send_message(channel, chat_id, f"Tool not found: {tool_call.name}", connector=connector)
                    return
                try:
                    result = await tool.handler(tool_call.arguments)
                except Exception as exc:
                    logger.exception("Tool execution failed name=%s", tool_call.name)
                    await self.send_message(
                        channel, chat_id, f"Tool `{tool_call.name}` failed: {exc}", connector=connector
                    )
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
                tool_results.append((tool_call, _TR(content=masked_content, data=result.data)))

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
                self.db.add_message(session_id, "assistant", follow_up.content)
                await self.send_message(channel, chat_id, follow_up.content, connector=connector)

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
    ) -> str:
        """Delegate a task to the DynamicOrchestrator and return the final reply."""

        async def send_update(message: str) -> None:
            await self.send_message(channel, chat_id, f"[update] {message}", connector=connector)

        orchestrator = DynamicOrchestrator(
            orchestrator_llm=self.orchestrator_llm,
            agent_llm=self.agent_llm,
            tool_registry=self.tool_registry,
            available_tool_names=allowed_tools,
            send_update_callback=send_update,
            request_approval_callback=None,
            max_orchestrator_iterations=agents_cfg.max_orchestrator_iterations,
            max_agent_iterations=agents_cfg.max_agent_iterations,
            skill_context=skill_context,
            skill_context_full=skill_context_full,
            agent_context=self.agent_context,
        )

        return await orchestrator.run(task=task, history=history)

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

    def _allowed_tools(self) -> list[str]:
        if self.policy.strictness == "strict":
            return []
        return list(self.tool_registry.list().keys())


def _build_agent_llm_client(config, agent_model_cfg, rate_limiter=None):
    """Build an LLM client for a specific agent role config.

    Fields left empty/None in ``agent_model_cfg`` inherit from ``config.llm``.
    The optional ``rate_limiter`` (TokenBucket) is shared across all clients.
    """
    provider = agent_model_cfg.provider or config.llm.provider
    model = agent_model_cfg.model or config.llm.model
    api_key = agent_model_cfg.api_key or config.llm.api_key
    reasoning_effort = agent_model_cfg.reasoning_effort

    if not api_key:
        raise RuntimeError("Agent LLM API key not configured")

    provider = provider.lower()
    if provider == "openai":
        return OpenAIClient(api_key, model, reasoning_effort=reasoning_effort, rate_limiter=rate_limiter)
    if provider == "claude":
        return ClaudeClient(api_key, model, rate_limiter=rate_limiter)
    if provider == "gemini":
        return GeminiClient(api_key, model, rate_limiter=rate_limiter)
    raise RuntimeError(f"Unknown LLM provider for agent: {provider}")


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
    api_key = config.llm.api_key
    if not api_key:
        raise RuntimeError("LLM API key not configured")
    if provider == "openai":
        return OpenAIClient(api_key, config.llm.model, reasoning_effort=config.llm.reasoning_effort, rate_limiter=rate_limiter)
    if provider == "claude":
        return ClaudeClient(api_key, config.llm.model, rate_limiter=rate_limiter)
    if provider == "gemini":
        return GeminiClient(api_key, config.llm.model, rate_limiter=rate_limiter)
    raise RuntimeError(f"Unknown LLM provider: {provider}")


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
