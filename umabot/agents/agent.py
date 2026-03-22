"""SpawnedAgent — a single specialist agent with its own agentic tool loop.

Created by the DynamicOrchestrator when it decides a sub-task requires a
dedicated specialist.  Each agent runs up to ``max_iterations`` LLM calls,
executing tool calls in between, and returns a final result string to the
orchestrator.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from umabot.llm.base import LLMClient, LLMResponse, compress_tool_output

logger = logging.getLogger("umabot.agents.agent")


class SpawnedAgent:
    """A specialist agent that runs its own multi-iteration tool loop.

    Args:
        role: Short label describing the agent's role (e.g. "Researcher").
        objective: Clear description of what this agent must achieve.
        system_prompt: Detailed instructions injected as the agent's system message.
        tool_names: Names of tools this agent is allowed to call.
        context: Extra context/data passed from the orchestrator.
        llm_client: LLM client (usually a cheaper/faster model).
        tool_registry: Registry to look up callable tool handlers.
        max_iterations: Maximum LLM→tool cycles before forcing a final answer.
        on_tool_call: Optional async callback invoked *before* each tool call;
                      receives (tool_name, arguments) and may raise to block the call.
    """

    def __init__(
        self,
        *,
        role: str,
        objective: str,
        system_prompt: str,
        tool_names: List[str],
        context: str = "",
        llm_client: LLMClient,
        tool_registry,
        max_iterations: int = 15,
        on_tool_call: Optional[Callable] = None,
    ) -> None:
        self.role = role
        self.objective = objective
        self.system_prompt = system_prompt
        self.tool_names = tool_names
        self.context = context
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.max_iterations = max_iterations
        self.on_tool_call = on_tool_call

    async def run(self) -> str:
        """Execute the agent's agentic tool loop and return a final result string."""
        user_content = f"Objective: {self.objective}"
        if self.context:
            user_content += f"\n\nContext:\n{self.context}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]

        tools_spec = self._build_tools_spec()
        logger.info(
            "Agent starting role=%s tools=%d max_iter=%d",
            self.role,
            len(tools_spec),
            self.max_iterations,
        )

        for iteration in range(self.max_iterations):
            # Keep system + first user message, then only the last 6 messages
            # to prevent conversation history from growing unboundedly and hitting rate limits.
            trimmed = _trim_messages(messages, keep_head=2, keep_tail=6)
            response = await self.llm_client.generate(
                trimmed, tools=tools_spec if tools_spec else None
            )
            logger.debug(
                "Agent role=%s iter=%d content_len=%d tool_calls=%d",
                self.role,
                iteration,
                len(response.content or ""),
                len(response.tool_calls),
            )
            messages.append(_assistant_message(response))

            if not response.tool_calls:
                # Agent is done
                return response.content or ""

            # Execute tool calls
            for tool_call in response.tool_calls:
                result_content = await self._execute_tool(tool_call.name, tool_call.arguments)
                stored_content = compress_tool_output(result_content)
                tool_message: Dict[str, Any] = {
                    "role": "tool",
                    "content": stored_content,
                    "name": tool_call.name,
                }
                if tool_call.id:
                    tool_message["tool_call_id"] = tool_call.id
                messages.append(tool_message)

        # Max iterations reached — force a final answer without tools
        logger.warning("Agent role=%s reached max_iterations=%d, forcing final answer", self.role, self.max_iterations)
        final = await self.llm_client.generate(messages, tools=None)
        return final.content or f"[{self.role}] reached iteration limit without completing the objective."

    async def _execute_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        if self.on_tool_call:
            try:
                await self.on_tool_call(name, arguments)
            except Exception as exc:
                return f"Tool blocked: {exc}"

        tool = self.tool_registry.get(name)
        if not tool:
            logger.warning("Agent role=%s requested unknown tool=%s", self.role, name)
            return f"Tool '{name}' not found."
        try:
            result = await tool.handler(arguments)
            return result.content or ""
        except Exception as exc:
            logger.exception("Agent role=%s tool=%s failed", self.role, name)
            return f"Tool '{name}' failed: {exc}"

    def _build_tools_spec(self) -> List[Dict[str, Any]]:
        specs = []
        for name in self.tool_names:
            tool = self.tool_registry.get(name)
            if not tool:
                continue
            specs.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.schema,
                }
            )
        return specs


def _trim_messages(
    messages: List[Dict[str, Any]],
    keep_head: int = 2,
    keep_tail: int = 6,
) -> List[Dict[str, Any]]:
    """Return messages with head + tail kept and middle trimmed."""
    if len(messages) <= keep_head + keep_tail:
        return messages
    return messages[:keep_head] + messages[-keep_tail:]


def _assistant_message(response: LLMResponse) -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": response.content or ""}
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id or "",
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in response.tool_calls
            if call.id
        ]
    return message
