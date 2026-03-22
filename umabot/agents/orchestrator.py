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
from typing import Any, Callable, Coroutine, Dict, List, Optional

from umabot.llm.base import LLMClient, LLMResponse, ToolCall

from .agent import SpawnedAgent, _assistant_message

logger = logging.getLogger("umabot.agents.orchestrator")

# ---------------------------------------------------------------------------
# Orchestrator system prompt
# ---------------------------------------------------------------------------
_ORCHESTRATOR_SYSTEM = """\
You are a dynamic task orchestrator for a personal AI assistant.

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

Available tools for you to call:
- spawn_agent       : Launch a specialist agent for a sub-task.
- send_update       : Send a progress message to the user right now.
- request_approval  : Pause and ask the user to approve a sensitive action.

Available tools you can grant to agents (tool_names list):
{tool_catalog}
{skill_section}"""


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
        max_orchestrator_iterations: int = 20,
        max_agent_iterations: int = 15,
        skill_context: str = "",
        skill_context_full: str = "",
    ) -> None:
        self.orchestrator_llm = orchestrator_llm
        self.agent_llm = agent_llm
        self.tool_registry = tool_registry
        self.available_tool_names = available_tool_names
        self.send_update_callback = send_update_callback
        self.request_approval_callback = request_approval_callback
        self.max_orchestrator_iterations = max_orchestrator_iterations
        self.max_agent_iterations = max_agent_iterations
        self.skill_context = skill_context
        # Full skill body injected into spawned agents' context (not into orchestrator prompt)
        self.skill_context_full = skill_context_full

    async def run(self, task: str, history: Optional[List[Dict[str, Any]]] = None) -> str:
        """Run the full orchestration for ``task`` and return the final reply.

        Args:
            task: The user's original message / request.
            history: Optional prior conversation messages for context.
        """
        tool_catalog = self._build_tool_catalog()
        skill_note = (
            f"\nACTIVE SKILL: {self.skill_context.splitlines()[0] if self.skill_context else ''}"
            "\n(Full skill instructions will be injected into each spawned agent automatically.)"
            if self.skill_context else ""
        )
        system_prompt = _ORCHESTRATOR_SYSTEM.format(
            tool_catalog=tool_catalog,
            skill_section=skill_note,
        )

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        # Inject recent history (stripped to assistant/user only — no tool messages)
        if history:
            for msg in history[-10:]:
                if msg.get("role") in ("user", "assistant"):
                    messages.append({"role": msg["role"], "content": msg.get("content") or ""})

        messages.append({"role": "user", "content": task})

        orchestrator_tools = self._orchestrator_tools_spec()

        logger.info("Orchestrator starting task_len=%d tools=%d", len(task), len(orchestrator_tools))

        for iteration in range(self.max_orchestrator_iterations):
            response = await self.orchestrator_llm.generate(messages, tools=orchestrator_tools)
            logger.debug(
                "Orchestrator iter=%d content_len=%d tool_calls=%d",
                iteration,
                len(response.content or ""),
                len(response.tool_calls),
            )
            messages.append(_assistant_message(response))

            if not response.tool_calls:
                # Orchestrator is done
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
        role = str(args.get("role", "Agent"))
        objective = str(args.get("objective", ""))
        system_prompt = str(args.get("system_prompt", f"You are a {role}. Complete the objective precisely."))
        tool_names = args.get("tools", [])
        context = str(args.get("context", ""))
        # Append key skill instructions to the agent's system_prompt.
        # We prepend a concrete quick-start template so the agent can write the
        # full script in a SINGLE shell.run call instead of exploring incrementally.
        if self.skill_context_full:
            skill_excerpt = self.skill_context_full[:2000]
            if len(self.skill_context_full) > 2000:
                skill_excerpt += "\n[...skill instructions truncated for brevity...]"
            quick_start = (
                "\n\nIMPORTANT — write the COMPLETE Node.js script in ONE shell.run call, then save it to a .js file and execute it. "
                "Do NOT run small exploratory commands. The environment already has node + docx module available via NODE_PATH. "
                "Use this pattern:\n"
                "shell.run: cat > /tmp/create_doc.js << 'EOF'\n"
                "const { Document, Packer, Paragraph, TextRun, HeadingLevel } = require('docx');\n"
                "const fs = require('fs');\n"
                "const doc = new Document({ sections: [{ children: [ /* your content */ ] }] });\n"
                "Packer.toBuffer(doc).then(b => { fs.writeFileSync('/tmp/output.docx', b); console.log('saved'); });\n"
                "EOF\nnode /tmp/create_doc.js"
            )
            system_prompt = system_prompt + quick_start + "\n\n" + skill_excerpt

        # Validate requested tools — only grant tools that are actually available
        valid_tool_names = [t for t in tool_names if t in self.available_tool_names]
        invalid = set(tool_names) - set(valid_tool_names)
        if invalid:
            logger.warning("Orchestrator granted invalid tools %s for agent role=%s (ignored)", invalid, role)

        logger.info("Spawning agent role=%s tools=%s", role, valid_tool_names)

        agent = SpawnedAgent(
            role=role,
            objective=objective,
            system_prompt=system_prompt,
            tool_names=valid_tool_names,
            context=context,
            llm_client=self.agent_llm,
            tool_registry=self.tool_registry,
            max_iterations=self.max_agent_iterations,
        )

        try:
            result = await agent.run()
            logger.info("Agent role=%s completed result_len=%d", role, len(result))
            return f"[{role} result]\n{result}"
        except Exception as exc:
            logger.exception("Agent role=%s failed", role)
            return f"[{role} failed]: {exc}"

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

    def _build_tool_catalog(self) -> str:
        lines = []
        for name in sorted(self.available_tool_names):
            tool = self.tool_registry.get(name)
            if tool:
                desc = tool.description or "(no description)"
                lines.append(f"  - {name}: {desc}")
        return "\n".join(lines) if lines else "  (no tools available)"

    def _orchestrator_tools_spec(self) -> List[Dict[str, Any]]:
        return [
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
