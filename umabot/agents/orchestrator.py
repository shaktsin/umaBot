"""DynamicOrchestrator — strong-model planner that decides team composition at runtime.

The orchestrator receives the user's task, reasons about what specialist agents
are needed (no hard-coded roles), spawns them via the ``spawn_agent`` tool, and
synthesises their results into a final reply.

Tools available to the orchestrator:
    spawn_agent      — create and run a specialist agent for a sub-task
    send_update      — push a progress message to the user mid-task
    request_approval — ask the user to confirm a sensitive action before proceeding
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

from umabot.llm.base import LLMClient, LLMResponse, ToolCall
from umabot.tools.registry import Attachment

from .agent import SpawnedAgent, _assistant_message

logger = logging.getLogger("umabot.agents.orchestrator")

# ---------------------------------------------------------------------------
# Orchestrator system prompt
# ---------------------------------------------------------------------------
_ORCHESTRATOR_SYSTEM = """\
You are a dynamic task orchestrator for a personal AI assistant.

Current date/time: {current_datetime} (UTC)
Current local date: {current_date}
Day of week: {day_of_week}
Start of this week (Monday): {week_start}
End of this week (Sunday): {week_end}

When the user refers to relative dates ("today", "this week", "tomorrow", "next Monday", etc.)
compute them from the current date above and pass concrete ISO 8601 timestamps to tools.

Your responsibilities:
1. Analyse the user's request and decide what specialist agents are needed.
2. Spawn agents with specific roles, objectives, system prompts, and tools.
3. Review the agents' results and synthesise a final, coherent response.
4. Keep the user informed with progress updates for long-running tasks.
5. Request explicit approval before any destructive or irreversible action.

Key principles:
- Do NOT use hard-coded agent roles. Reason dynamically about what each
  specific task requires.  A task might need one agent or five.
- Each spawned agent runs an independent tool loop and returns a result.
- You coordinate; agents execute.
- Be efficient: avoid spawning redundant agents.
- IMPORTANT: You cannot execute tools directly. To run shell commands,
  write files, or do any work requiring a tool — you MUST spawn an agent
  and give it the appropriate tools from the catalog. Never claim a tool
  is unavailable if it appears in the catalog below.
- After receiving agent results, always synthesise and send the final answer
  yourself — do not rely on another spawn_agent call for synthesis.

File-writing rules (CRITICAL):
- NEVER combine skill loading + large file generation in one agent spawn. Keep them separate.
- For writing LARGE files (HTML, code > 500 chars): grant shell.run to the agent.
  Have it write the file with: python3 -c "open('path','w').write('''...content...''')"
  Do NOT use file.write for large content — the JSON payload size causes API timeouts.
- Always set workspace= in spawn_agent so the agent operates in the correct directory.
- For website builds: (1) skill load agent → (2) code generation + shell.run write agent
  → (3) server start agent → (4) playwright screenshot agent. Keep these as separate spawns.
- For screenshot/image deliverables: return at least one image attachment in the final admin reply.
  A filename-only message is not sufficient; you must call screenshot/image tools that emit attachments.
- Never claim that you cannot attach images. Tool-generated attachments are forwarded automatically.

Available tools for you to call:
- spawn_agent       : Launch a specialist agent for a sub-task.
- send_update       : Send a progress message to the user right now.
- request_approval  : Pause and ask the user to approve a sensitive action.

Available tools you can grant to agents (tool_names list):
{tool_catalog}

Available workspaces (pass the name via spawn_agent workspace= so the agent operates in the right directory):
{workspace_catalog}

Available skills (agents can load full instructions via skill.get_instructions):
{skills_catalog}
{skill_section}{agent_context_section}"""


class DynamicOrchestrator:
    """Orchestrates a team of dynamically-decided specialist agents.

    Args:
        orchestrator_llm: Strong reasoning model (e.g. o3, Claude Opus).
        agent_llm: Cheaper model used for spawned agents (e.g. gpt-4o).
        tool_registry: Registry of all callable tools.
        available_tool_names: Names of tools that agents may be granted.
        send_update_callback: ``async (message: str) -> None`` — delivers a
            progress message to the user's channel.
        request_approval_callback: ``async (reason, action_summary) -> bool``
            — asks the user for confirmation; returns True if approved.
        max_orchestrator_iterations: Max LLM cycles for the orchestrator itself.
        max_agent_iterations: Max LLM cycles per spawned agent.
        on_event: Optional callback for structured multi-agent telemetry.
    """

    def __init__(
        self,
        *,
        orchestrator_llm: LLMClient,
        agent_llm: LLMClient,
        tool_registry,
        available_tool_names: List[str],
        send_update_callback: Callable[[str], Coroutine],
        request_approval_callback: Optional[Callable[[str, str], Coroutine]] = None,
        disable_approval: bool = False,
        max_orchestrator_iterations: int = 20,
        max_agent_iterations: int = 15,
        role_max_iterations: Optional[Dict[str, int]] = None,
        skill_context: str = "",
        skill_context_full: str = "",
        agent_context: str = "",
        skill_registry=None,
        workspaces: Optional[List] = None,
        run_id: str = "",
        on_event: Optional[Callable[[str, Dict[str, Any]], Coroutine]] = None,
        on_agent_tool_call: Optional[Callable[[str, Dict[str, Any]], Coroutine]] = None,
    ) -> None:
        self.orchestrator_llm = orchestrator_llm
        self.agent_llm = agent_llm
        self.tool_registry = tool_registry
        self.available_tool_names = available_tool_names
        self.send_update_callback = send_update_callback
        self.request_approval_callback = request_approval_callback
        self.disable_approval = disable_approval
        self.max_orchestrator_iterations = max_orchestrator_iterations
        self.max_agent_iterations = max_agent_iterations
        self.role_max_iterations: Dict[str, int] = role_max_iterations or {}
        self.skill_context = skill_context
        self.skill_context_full = skill_context_full  # retained for API compat, no longer pre-injected
        # User-defined standing context from AGENT.md
        self.agent_context = agent_context
        self.skill_registry = skill_registry
        self.workspaces: List = workspaces or []
        self._collected_attachments: List[Attachment] = []
        self.run_id = run_id
        self.on_event = on_event
        self.on_agent_tool_call = on_agent_tool_call
        self._agent_seq = 0

    async def run(self, task: str, history: Optional[List[Dict[str, Any]]] = None) -> str:
        """Run the full orchestration for ``task`` and return the final reply.

        Args:
            task: The user's original message / request.
            history: Optional prior conversation messages for context.
        """
        self._collected_attachments = []
        tool_catalog = self._build_tool_catalog()
        skill_note = f"\n{self.skill_context}" if self.skill_context else ""

        now_utc = datetime.now(timezone.utc)
        # Monday=0 … Sunday=6; back-calculate to Monday
        week_start = now_utc.date() - timedelta(days=now_utc.weekday())
        week_end = week_start + timedelta(days=6)

        agent_context_section = (
            f"\n\n--- User-defined agent context (from AGENT.md) ---\n{self.agent_context}\n---"
            if self.agent_context.strip() else ""
        )

        from umabot.tools.workspace import workspace_summary
        workspace_catalog = workspace_summary(self.workspaces)
        skills_catalog = self._build_skills_catalog()

        system_prompt = _ORCHESTRATOR_SYSTEM.format(
            current_datetime=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            current_date=now_utc.strftime("%Y-%m-%d"),
            day_of_week=now_utc.strftime("%A"),
            week_start=week_start.isoformat(),
            week_end=week_end.isoformat(),
            tool_catalog=tool_catalog,
            workspace_catalog=workspace_catalog,
            skills_catalog=skills_catalog,
            skill_section=skill_note,
            agent_context_section=agent_context_section,
        )

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        # Inject recent history (stripped to assistant/user only — no tool messages)
        if history:
            for msg in history[-10:]:
                if msg.get("role") in ("user", "assistant"):
                    messages.append({"role": msg["role"], "content": msg.get("content") or ""})

        messages.append({"role": "user", "content": task})
        await self._emit(
            "multi_agent_run_started",
            {
                "run_id": self.run_id,
                "task": task,
                "status": "running",
                "started_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "root_node_id": "orchestrator",
                "nodes": [
                    {
                        "node_id": "orchestrator",
                        "parent_node_id": "",
                        "role": "orchestrator",
                        "status": "running",
                        "model": getattr(self.orchestrator_llm, "model", ""),
                        "workspace": "",
                        "objective": "Plan, spawn agents, and synthesize output.",
                    }
                ],
            },
        )

        orchestrator_tools = self._orchestrator_tools_spec()

        logger.info("Orchestrator starting task_len=%d tools=%d", len(task), len(orchestrator_tools))

        for iteration in range(self.max_orchestrator_iterations):
            response = await self.orchestrator_llm.generate(messages, tools=orchestrator_tools)
            await self._emit(
                "multi_agent_node_log",
                {
                    "run_id": self.run_id,
                    "node_id": "orchestrator",
                    "parent_node_id": "",
                    "role": "orchestrator",
                    "event_type": "orchestrator.iteration",
                    "message": f"Iteration {iteration + 1} completed",
                    "payload": {
                        "iteration": iteration + 1,
                        "tool_calls": len(response.tool_calls or []),
                        "content_len": len(response.content or ""),
                    },
                },
            )
            logger.debug(
                "Orchestrator iter=%d content_len=%d tool_calls=%d",
                iteration,
                len(response.content or ""),
                len(response.tool_calls),
            )
            messages.append(_assistant_message(response))

            if not response.tool_calls:
                # Orchestrator is done
                await self._emit(
                    "multi_agent_run_completed",
                    {
                        "run_id": self.run_id,
                        "status": "completed",
                        "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "summary": response.content or "",
                    },
                )
                return response.content or ""

            # Process each orchestrator tool call
            for tool_call in response.tool_calls:
                result_content = await self._handle_orchestrator_tool(tool_call)
                tool_message: Dict[str, Any] = {
                    "role": "tool",
                    "content": result_content,
                    "name": tool_call.name,
                }
                if tool_call.id:
                    tool_message["tool_call_id"] = tool_call.id
                messages.append(tool_message)

        # Max iterations reached
        logger.warning("Orchestrator reached max_iterations=%d", self.max_orchestrator_iterations)
        final = await self.orchestrator_llm.generate(messages, tools=None)
        await self._emit(
            "multi_agent_run_completed",
            {
                "run_id": self.run_id,
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "summary": final.content or "",
            },
        )
        return final.content or "Orchestrator reached iteration limit without completing the task."

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _handle_orchestrator_tool(self, tool_call: ToolCall) -> str:
        name = tool_call.name
        args = tool_call.arguments or {}

        if name == "spawn_agent":
            return await self._spawn_agent(args)
        if name == "send_update":
            return await self._send_update(args)
        if name == "request_approval":
            return await self._request_approval(args)

        return f"Unknown orchestrator tool: {name}"

    async def _spawn_agent(self, args: Dict[str, Any]) -> str:
        from umabot.tools.workspace import resolve_workspace, set_active_workspace

        role = str(args.get("role", "Agent"))
        objective = str(args.get("objective", ""))
        system_prompt = str(args.get("system_prompt", f"You are a {role}. Complete the objective precisely."))
        tool_names = args.get("tools", [])
        context = str(args.get("context", ""))

        # Set the workspace for this agent's execution context
        workspace_name = str(args.get("workspace", "")).strip()
        ws = resolve_workspace(workspace_name, self.workspaces)
        set_active_workspace(ws)
        workspace_note = (
            f"\n\nActive workspace: {ws.name} ({ws.path})\n"
            f"Relative file paths are resolved against this directory.\n"
            f"shell.run cwd defaults to this path."
        )
        system_prompt = system_prompt + workspace_note
        # Skill instructions are loaded on demand via skill.get_instructions —
        # do NOT pre-inject them here to avoid large upfront token usage.

        # Validate requested tools — only grant tools that are actually available
        valid_tool_names = [t for t in tool_names if t in self.available_tool_names]
        invalid = set(tool_names) - set(valid_tool_names)
        if invalid:
            logger.warning("Orchestrator granted invalid tools %s for agent role=%s (ignored)", invalid, role)

        logger.info("Spawning agent role=%s tools=%s", role, valid_tool_names)
        self._agent_seq += 1
        node_id = f"agent-{self._agent_seq}"
        await self._emit(
            "multi_agent_node_added",
            {
                "run_id": self.run_id,
                "node_id": node_id,
                "parent_node_id": "orchestrator",
                "role": role,
                "status": "queued",
                "objective": objective,
                "workspace": ws.name if ws else "",
                "model": getattr(self.agent_llm, "model", ""),
            },
        )

        agent_max_iter = self.role_max_iterations.get(role) or self.max_agent_iterations
        agent = SpawnedAgent(
            role=role,
            objective=objective,
            system_prompt=system_prompt,
            tool_names=valid_tool_names,
            context=context,
            llm_client=self.agent_llm,
            tool_registry=self.tool_registry,
            max_iterations=agent_max_iter,
            on_tool_call=self.on_agent_tool_call,
            on_event=self.on_event,
            run_id=self.run_id,
            agent_id=node_id,
            parent_agent_id="orchestrator",
        )

        try:
            result = await agent.run()
            if result.attachments:
                self._collected_attachments.extend(result.attachments)
            logger.info(
                "Agent role=%s completed result_len=%d attachments=%d",
                role,
                len(result.content or ""),
                len(result.attachments),
            )
            await self._emit(
                "multi_agent_node_status",
                {
                    "run_id": self.run_id,
                    "node_id": node_id,
                    "parent_node_id": "orchestrator",
                    "role": role,
                    "status": "completed",
                },
            )
            return f"[{role} result]\n{result.content}"
        except Exception as exc:
            logger.exception("Agent role=%s failed", role)
            await self._emit(
                "multi_agent_node_status",
                {
                    "run_id": self.run_id,
                    "node_id": node_id,
                    "parent_node_id": "orchestrator",
                    "role": role,
                    "status": "failed",
                    "error": str(exc),
                },
            )
            await self._emit(
                "multi_agent_node_log",
                {
                    "run_id": self.run_id,
                    "node_id": node_id,
                    "parent_node_id": "orchestrator",
                    "role": role,
                    "event_type": "agent.error",
                    "message": str(exc),
                    "payload": {},
                },
            )
            return f"[{role} failed]: {exc}"

    @property
    def collected_attachments(self) -> List[Attachment]:
        return list(self._collected_attachments)

    async def _emit(self, event_name: str, data: Dict[str, Any]) -> None:
        if not self.on_event:
            return
        try:
            await self.on_event(event_name, data)
        except Exception:
            pass

    async def _send_update(self, args: Dict[str, Any]) -> str:
        message = str(args.get("message", ""))
        if message:
            try:
                await self.send_update_callback(message)
            except Exception as exc:
                logger.warning("send_update callback failed: %s", exc)
        return "Update sent."

    async def _request_approval(self, args: Dict[str, Any]) -> str:
        reason = str(args.get("reason", ""))
        action_summary = str(args.get("action_summary", ""))

        if self.request_approval_callback:
            try:
                approved = await self.request_approval_callback(reason, action_summary)
                return "approved" if approved else "denied — do not proceed with this action"
            except Exception as exc:
                logger.warning("request_approval callback failed: %s", exc)
                return "approval request failed — treat as denied"

        # No callback configured — auto-deny for safety
        return "denied — approval callback not configured"

    def _build_skills_catalog(self) -> str:
        if not self.skill_registry:
            return "  (no skills loaded)"
        skills = self.skill_registry.list()
        if not skills:
            return "  (no skills loaded)"
        lines = []
        for skill in sorted(skills, key=lambda s: s.metadata.name):
            lines.append(f"  - {skill.metadata.name}: {skill.metadata.description[:120]}")
        return "\n".join(lines)

    def _build_tool_catalog(self) -> str:
        lines = []
        for name in sorted(self.available_tool_names):
            tool = self.tool_registry.get(name)
            if tool:
                desc = tool.description or "(no description)"
                lines.append(f"  - {name}: {desc}")
        return "\n".join(lines) if lines else "  (no tools available)"

    def _orchestrator_tools_spec(self) -> List[Dict[str, Any]]:
        tools = [
            {
                "name": "spawn_agent",
                "description": (
                    "Create and run a specialist agent to handle a specific sub-task. "
                    "The agent runs its own tool loop and returns a result. "
                    "Decide the role, objective, and which tools the agent needs."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "role": {
                            "type": "string",
                            "description": "Short label for the agent's role, e.g. 'Researcher', 'File Writer', 'Data Analyst'.",
                        },
                        "objective": {
                            "type": "string",
                            "description": "Clear, specific description of what the agent must achieve.",
                        },
                        "system_prompt": {
                            "type": "string",
                            "description": "Detailed system instructions for the agent. Be specific about its responsibilities and constraints.",
                        },
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of tool names from the available tool catalog that this agent may use.",
                        },
                        "context": {
                            "type": "string",
                            "description": "Optional extra context, data, or partial results to pass to the agent.",
                        },
                        "workspace": {
                            "type": "string",
                            "description": (
                                "Name of the workspace this agent should operate in. "
                                "Must match a name from the available workspaces list. "
                                "Leave empty to use the default workspace."
                            ),
                        },
                    },
                    "required": ["role", "objective", "system_prompt", "tools"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "send_update",
                "description": "Send a progress update message to the user right now (non-blocking).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Short progress update to display to the user.",
                        },
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "request_approval",
                "description": (
                    "Pause and ask the user to approve a sensitive or irreversible action. "
                    "Returns 'approved' or 'denied'. Do not proceed if denied."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Why approval is needed.",
                        },
                        "action_summary": {
                            "type": "string",
                            "description": "Clear summary of what will happen if the user approves.",
                        },
                    },
                    "required": ["reason", "action_summary"],
                    "additionalProperties": False,
                },
            },
        ]
        if self.disable_approval:
            tools = [t for t in tools if t["name"] != "request_approval"]
        return tools
