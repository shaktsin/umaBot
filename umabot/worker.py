from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from umabot.llm import ClaudeClient, GeminiClient, OpenAIClient
from umabot.policy import PolicyEngine
from umabot.skills import SkillRegistry
from umabot.storage import Database, Queue
from umabot.tasks.parser import parse_control_task_request
from umabot.tasks.schedule import compute_initial_next_run_at, compute_next_run_at
from umabot.tools import ToolRegistry


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
        send_message,
        send_control_message,
    ) -> None:
        self.config = config
        self.db = db
        self.queue = queue
        self.tool_registry = tool_registry
        self.policy = policy
        self.skill_registry = skill_registry
        self.send_message = send_message
        self.send_control_message = send_control_message
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._chat_locks: Dict[str, asyncio.Lock] = {}
        self._context_state = SkillToolContextState(revision=0, fingerprint="", system_messages=[], dynamic_tool_map={})

        self.llm_client = _build_llm_client(config)

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            job = await self.queue.claim()
            if not job:
                await asyncio.sleep(0.5)
                continue
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
            self._context_state = _refresh_context_state(self._context_state, self.skill_registry, self.tool_registry)
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
            if self._context_state.system_messages:
                messages = list(self._context_state.system_messages) + messages
            dynamic_tool_map = self._context_state.dynamic_tool_map
            allowed_tools = self._allowed_tools(dynamic_tool_map)
            tools_spec = _build_tool_specs(self.tool_registry, allowed_tools, self.skill_registry, dynamic_tool_map)

            logger.debug(
                "LLM request kind=%s chat_id=%s session_id=%s messages=%s tools=%s ctx_rev=%s dyn_tools=%s",
                kind,
                chat_id,
                session_id,
                len(messages),
                len(tools_spec),
                self._context_state.revision,
                len(dynamic_tool_map),
            )
            try:
                response = await self.llm_client.generate(messages, tools=tools_spec)
            except Exception as exc:
                logger.exception("LLM request failed chat_id=%s error=%s", chat_id, exc)
                await self.send_message(channel, chat_id, "LLM request failed. Check logs.")
                return
            logger.debug(
                "LLM response chat_id=%s content_len=%s tool_calls=%s",
                chat_id,
                len(response.content or ""),
                len(response.tool_calls),
            )
            logger.debug("LLM response content chat_id=%s content=%s", chat_id, response.content)
            for call in response.tool_calls:
                if call.name == "skills.run_script" or call.name.startswith("skill_"):
                    script_name = str(call.arguments.get("script", "")).strip()
                    input_obj = call.arguments.get("input")
                    input_keys = sorted(list(input_obj.keys())) if isinstance(input_obj, dict) else []
                    logger.debug(
                        "LLM skill-call preview chat_id=%s skill=%s script=%s input_keys=%s has_input=%s",
                        chat_id,
                        call.arguments.get("skill"),
                        script_name,
                        input_keys,
                        isinstance(input_obj, dict),
                    )
                    if script_name == "create":
                        has_title = isinstance(input_obj, dict) and bool(str(input_obj.get("title", "")).strip())
                        logger.debug(
                            "LLM skill-call create validation chat_id=%s has_title=%s",
                            chat_id,
                            has_title,
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
                logger.debug("Tool call requested name=%s args=%s", tool_call.name, tool_call.arguments)
                effective_name = tool_call.name
                effective_args = tool_call.arguments
                if tool_call.name in dynamic_tool_map:
                    skill_name, script_name = dynamic_tool_map[tool_call.name]
                    logger.debug(
                        "Dynamic tool mapped llm_tool=%s -> skills.run_script skill=%s script=%s",
                        tool_call.name,
                        skill_name,
                        script_name,
                    )
                    effective_name = "skills.run_script"
                    effective_args = {
                        "skill": skill_name,
                        "script": script_name,
                        "input": tool_call.arguments if isinstance(tool_call.arguments, dict) else {},
                    }
                decision = self.policy.evaluate(
                    {"name": effective_name, "arguments": effective_args},
                    allowed_tools + (["skills.run_script"] if "skills.run_script" in self.tool_registry.list() else []),
                    chat_id=chat_id,
                    channel=channel,
                    session_id=session_id,
                    message_id=assistant_message_id,
                    messages=messages,
                )
                if decision.require_confirmation:
                    logger.info("Tool confirmation required name=%s token=%s", tool_call.name, decision.token)
                    prompt = f"Reply YES {decision.token} to confirm"
                    await self.send_control_message(channel, chat_id, prompt)
                    self.db.add_audit(
                        "tool_confirmation_requested",
                        {"chat_id": chat_id, "tool": tool_call.name, "token": decision.token},
                    )
                    return
                if not decision.allowed:
                    logger.info("Tool denied name=%s reason=%s", tool_call.name, decision.reason)
                    await self.send_message(channel, chat_id, f"Tool denied: {decision.reason}", connector=connector)
                    return
                tool = self.tool_registry.get(effective_name)
                if not tool:
                    await self.send_message(channel, chat_id, f"Tool not found: {tool_call.name}", connector=connector)
                    return
                result = await tool.handler(effective_args)
                logger.debug("Tool result name=%s content_len=%s", tool_call.name, len(result.content or ""))
                self.db.add_tool_call(
                    assistant_message_id,
                    tool_call.name,
                    effective_args,
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
            if follow_up.content:
                self.db.add_message(session_id, "assistant", follow_up.content)
                await self.send_message(channel, chat_id, follow_up.content, connector=connector)

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
        self.skill_registry.refresh()
        self._context_state = _refresh_context_state(self._context_state, self.skill_registry, self.tool_registry)
        if self._context_state.system_messages:
            messages = list(self._context_state.system_messages) + messages
        dynamic_tool_map = self._context_state.dynamic_tool_map
        allowed_tools = self._allowed_tools(dynamic_tool_map)
        tools_spec = _build_tool_specs(self.tool_registry, allowed_tools, self.skill_registry, dynamic_tool_map)
        logger.debug(
            "Task run LLM request task_id=%s run_id=%s messages=%s tools=%s ctx_rev=%s dyn_tools=%s",
            task_id,
            run_id,
            len(messages),
            len(tools_spec),
            self._context_state.revision,
            len(dynamic_tool_map),
        )
        try:
            response = await self.llm_client.generate(messages, tools=tools_spec)
            assistant_message_id = self.db.add_message(session_id, "assistant", response.content or "")
            messages.append(_assistant_message(response))
            final_content = response.content or ""
            if response.tool_calls:
                tool_results = []
                for tool_call in response.tool_calls:
                    effective_name = tool_call.name
                    effective_args = tool_call.arguments
                    if tool_call.name in dynamic_tool_map:
                        skill_name, script_name = dynamic_tool_map[tool_call.name]
                        logger.debug(
                            "Dynamic tool mapped llm_tool=%s -> skills.run_script skill=%s script=%s",
                            tool_call.name,
                            skill_name,
                            script_name,
                        )
                        effective_name = "skills.run_script"
                        effective_args = {
                            "skill": skill_name,
                            "script": script_name,
                            "input": tool_call.arguments if isinstance(tool_call.arguments, dict) else {},
                        }
                    decision = self.policy.evaluate(
                        {"name": effective_name, "arguments": effective_args},
                        allowed_tools + (["skills.run_script"] if "skills.run_script" in self.tool_registry.list() else []),
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
                    tool = self.tool_registry.get(effective_name)
                    if not tool:
                        text = f"Task #{task_id} missing tool '{tool_call.name}'"
                        self.db.fail_task_run(run_id=run_id, task_id=task_id, error=text)
                        await self.send_control_message("system", chat_id, text)
                        return
                    result = await tool.handler(effective_args)
                    self.db.add_tool_call(
                        assistant_message_id,
                        tool_call.name,
                        effective_args,
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
        session_id = pending["session_id"]
        message_id = pending["message_id"]
        tool_call = pending["tool_call"]
        messages = pending["messages"]

        tool = self.tool_registry.get(tool_call["name"])
        if not tool:
            await self.send_message(channel, chat_id, "Tool not found for confirmation.")
            return
        result = await tool.handler(tool_call["arguments"])
        self.db.add_tool_call(
            message_id,
            tool_call["name"],
            tool_call["arguments"],
            {"content": result.content, "data": result.data},
        )
        tool_message = {
            "role": "tool",
            "content": result.content,
            "name": tool_call["name"],
        }
        messages.append(tool_message)
        follow_up = await self.llm_client.generate(messages, tools=None)
        if follow_up.content:
            self.db.add_message(session_id, "assistant", follow_up.content)
            await self.send_message(channel, chat_id, follow_up.content)

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

    def _allowed_tools(self, dynamic_tool_map: Optional[Dict[str, tuple[str, str]]] = None) -> list[str]:
        if self.policy.strictness == "strict":
            return []
        names = [name for name in self.tool_registry.list().keys() if name != "skills.run_script"]
        if dynamic_tool_map:
            names.extend(dynamic_tool_map.keys())
        return names


def _build_llm_client(config):
    provider = config.llm.provider.lower()
    api_key = config.llm.api_key
    if not api_key:
        raise RuntimeError("LLM API key not configured")
    if provider == "openai":
        return OpenAIClient(api_key, config.llm.model)
    if provider == "claude":
        return ClaudeClient(api_key, config.llm.model)
    if provider == "gemini":
        return GeminiClient(api_key, config.llm.model)
    raise RuntimeError(f"Unknown LLM provider: {provider}")


def _build_tool_specs(
    registry: ToolRegistry,
    allowed_tools: list[str],
    skill_registry: Optional[SkillRegistry] = None,
    dynamic_tool_map: Optional[Dict[str, tuple[str, str]]] = None,
) -> List[Dict[str, Any]]:
    specs = []
    dynamic_schema_map = _dynamic_skill_tool_schemas(skill_registry) if skill_registry else {}
    for tool in registry.list().values():
        if tool.name == "skills.run_script":
            continue
        if tool.name not in allowed_tools:
            continue
        specs.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.schema,
            }
        )
    for dyn_name, schema in dynamic_schema_map.items():
        if dyn_name not in allowed_tools:
            continue
        skill_name, script_name = (dynamic_tool_map or {}).get(dyn_name, ("", ""))
        specs.append(
            {
                "name": dyn_name,
                "description": f"Run skill script {skill_name}.{script_name}",
                "parameters": schema,
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
class SkillToolContextState:
    revision: int
    fingerprint: str
    system_messages: List[Dict[str, str]]
    dynamic_tool_map: Dict[str, tuple[str, str]]


def _refresh_context_state(
    previous: SkillToolContextState,
    skill_registry: SkillRegistry,
    tool_registry: ToolRegistry,
) -> SkillToolContextState:
    fingerprint = _context_fingerprint(skill_registry, tool_registry)
    if fingerprint == previous.fingerprint:
        return previous
    dynamic_tool_map = _dynamic_skill_tool_map(skill_registry)
    system_messages = _build_skill_system_messages(skill_registry, dynamic_tool_map)
    return SkillToolContextState(
        revision=previous.revision + 1,
        fingerprint=fingerprint,
        system_messages=system_messages,
        dynamic_tool_map=dynamic_tool_map,
    )


def _context_fingerprint(skill_registry: SkillRegistry, tool_registry: ToolRegistry) -> str:
    tools = sorted(tool_registry.list().keys())
    skills_repr: List[Dict[str, Any]] = []
    for skill in sorted(skill_registry.list(), key=lambda s: s.metadata.name):
        scripts = sorted(skill.metadata.scripts.items(), key=lambda kv: kv[0])
        skills_repr.append(
            {
                "name": skill.metadata.name,
                "version": skill.metadata.version,
                "allowed_tools": list(skill.metadata.allowed_tools),
                "scripts": scripts,
            }
        )
    raw = json.dumps({"tools": tools, "skills": skills_repr}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_skill_system_messages(
    skill_registry: SkillRegistry,
    dynamic_tool_map: Dict[str, tuple[str, str]],
) -> List[Dict[str, str]]:
    skills = skill_registry.list()
    if not skills:
        return []
    lines: List[str] = [
        "Skill Tools Catalog (updated runtime state):",
        "Select the best matching skill tool and pass valid structured input.",
        "If required fields are unknown, ask one clarification question.",
        "Available tools:",
    ]
    for dyn_tool_name in sorted(dynamic_tool_map.keys()):
        skill_name, script_name = dynamic_tool_map[dyn_tool_name]
        skill = skill_registry.get(skill_name)
        if not skill:
            continue
        script_spec = skill.metadata.scripts.get(script_name, {})
        req = script_spec.get("input_schema", {}).get("required", [])
        examples = script_spec.get("examples", [])
        example_hint = ""
        if examples and isinstance(examples, list):
            first = examples[0]
            if isinstance(first, dict):
                example_hint = f" example={json.dumps(first, ensure_ascii=True)}"
        lines.append(
            f"- {dyn_tool_name}: skill={skill_name} script={script_name}"
            f" required={req}{example_hint}"
        )
    return [{"role": "system", "content": "\n".join(lines)}]


def _dynamic_skill_tool_map(skill_registry: SkillRegistry) -> Dict[str, tuple[str, str]]:
    mapping: Dict[str, tuple[str, str]] = {}
    for skill in skill_registry.list():
        for script_name, script_spec in skill.metadata.scripts.items():
            if not isinstance(script_spec, dict):
                continue
            mapping[_dynamic_skill_tool_name(skill.metadata.name, script_name)] = (
                skill.metadata.name,
                script_name,
            )
    return mapping


def _dynamic_skill_tool_schemas(skill_registry: SkillRegistry) -> Dict[str, Dict[str, Any]]:
    schemas: Dict[str, Dict[str, Any]] = {}
    for skill in skill_registry.list():
        for script_name, script_spec in skill.metadata.scripts.items():
            if not isinstance(script_spec, dict):
                continue
            input_schema = script_spec.get("input_schema")
            if not isinstance(input_schema, dict) or not input_schema:
                input_schema = {"type": "object", "additionalProperties": True}
            if input_schema.get("type") != "object":
                input_schema = {"type": "object", "additionalProperties": True}
            schemas[_dynamic_skill_tool_name(skill.metadata.name, script_name)] = input_schema
    return schemas


def _dynamic_skill_tool_name(skill_name: str, script_name: str) -> str:
    def _safe(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value)

    return f"skill_{_safe(skill_name)}_{_safe(script_name)}"
