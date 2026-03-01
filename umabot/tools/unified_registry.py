"""Unified tool registry managing built-in tools, skills, and MCP servers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional

from .registry import Tool, ToolResult

if TYPE_CHECKING:
    from umabot.skills.registry import SkillRegistry
    from umabot.skills.runtime import SkillRuntime

logger = logging.getLogger("umabot.tools.unified")


class ToolSource(Enum):
    """Source of a tool."""
    BUILTIN = "builtin"
    SKILL = "skill"
    MCP = "mcp"


@dataclass
class ToolInfo:
    """Complete tool information for LLM."""
    name: str
    source: ToolSource
    description: str
    schema: dict
    metadata: dict = field(default_factory=dict)


class UnifiedToolRegistry:
    """Single registry for all tool types (built-in, skills, MCP)."""

    def __init__(self):
        self._builtin_tools: Dict[str, Tool] = {}
        self._skill_registry: Optional[SkillRegistry] = None
        self._skill_runtime: Optional[SkillRuntime] = None
        self._mcp_registry: Optional[Any] = None  # Will be MCPRegistry when implemented

    # ==================== Built-in Tools ====================

    def register_builtin(self, tool: Tool):
        """Register built-in tool."""
        self._builtin_tools[tool.name] = tool
        logger.debug(f"Registered built-in tool: {tool.name}")

    def unregister_builtin(self, name: str):
        """Remove built-in tool."""
        if name in self._builtin_tools:
            self._builtin_tools.pop(name)
            logger.debug(f"Unregistered built-in tool: {name}")

    # ==================== Skills ====================

    def set_skill_registry(self, registry: SkillRegistry):
        """Connect skill registry."""
        self._skill_registry = registry
        logger.debug("Connected skill registry")

    def set_skill_runtime(self, runtime: SkillRuntime):
        """Connect skill runtime."""
        self._skill_runtime = runtime
        logger.debug("Connected skill runtime")

    # ==================== MCP ====================

    def set_mcp_registry(self, registry: Any):
        """Connect MCP registry."""
        self._mcp_registry = registry
        logger.debug("Connected MCP registry")

    # ==================== Unified Interface ====================

    def get_all_tools(self) -> Dict[str, ToolInfo]:
        """Get all available tools for LLM."""
        tools = {}

        # 1. Built-in tools
        for name, tool in self._builtin_tools.items():
            tools[name] = ToolInfo(
                name=name,
                source=ToolSource.BUILTIN,
                description=tool.description or "",
                schema=tool.schema,
                metadata={
                    "risk_level": tool.risk_level
                }
            )

        # 2. Skill tools (dynamic)
        if self._skill_registry:
            skill_tools = self._build_skill_tools()
            for name, info in skill_tools.items():
                tools[name] = info

        # 3. MCP tools
        if self._mcp_registry:
            try:
                mcp_tools = self._mcp_registry.get_all_tools()
                for name, tool_def in mcp_tools.items():
                    tools[name] = ToolInfo(
                        name=name,
                        source=ToolSource.MCP,
                        description=tool_def.get("description", ""),
                        schema=tool_def["schema"],
                        metadata={
                            "server": tool_def["server"],
                            "original_name": tool_def["tool"]
                        }
                    )
            except Exception as e:
                logger.error(f"Error getting MCP tools: {e}")

        return tools

    def _build_skill_tools(self) -> Dict[str, ToolInfo]:
        """Build tool definitions from skills."""
        tools = {}

        if not self._skill_registry:
            return tools

        for skill in self._skill_registry.list():
            for script_name, script_spec in skill.metadata.scripts.items():
                # Generate tool name: skill_{skill_name}_{script_name}
                tool_name = f"skill_{skill.metadata.name}_{script_name}"

                tools[tool_name] = ToolInfo(
                    name=tool_name,
                    source=ToolSource.SKILL,
                    description=script_spec.get("description", ""),
                    schema=script_spec.get("input_schema", {}),
                    metadata={
                        "skill": skill.metadata.name,
                        "script": script_name,
                        "risk_level": skill.metadata.risk_level
                    }
                )

        return tools

    async def execute_tool(self, name: str, arguments: dict) -> ToolResult:
        """Execute any tool by name."""
        # 1. Built-in tool?
        if name in self._builtin_tools:
            tool = self._builtin_tools[name]
            logger.debug(f"Executing built-in tool: {name}")
            result = await tool.handler(arguments)
            return result

        # 2. Skill tool?
        if name.startswith("skill_"):
            if not self._skill_runtime:
                raise ValueError("Skill runtime not configured")

            # Parse: skill_{skill_name}_{script_name}
            parts = name.split("_", 2)
            if len(parts) < 3:
                raise ValueError(f"Invalid skill tool name: {name}")

            skill_name = parts[1]
            script_name = parts[2]

            logger.debug(f"Executing skill: {skill_name}.{script_name}")

            skill_result = await self._skill_runtime.run_script(
                skill_name=skill_name,
                script=script_name,
                payload=arguments
            )

            # Convert SkillRunResult to ToolResult
            return ToolResult(
                content=skill_result.output,
                data=skill_result.data
            )

        # 3. MCP tool?
        if name.startswith("mcp_"):
            if not self._mcp_registry:
                raise ValueError("MCP not configured")

            logger.debug(f"Executing MCP tool: {name}")

            content = await self._mcp_registry.call_tool(name, arguments)

            # Convert MCP content to ToolResult format
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))

            return ToolResult(
                content="\n".join(text_parts) if text_parts else "",
                data={"mcp_content": content}
            )

        raise ValueError(f"Unknown tool: {name}")

    def get_tool_source(self, name: str) -> Optional[ToolSource]:
        """Determine source of a tool."""
        if name in self._builtin_tools:
            return ToolSource.BUILTIN
        elif name.startswith("skill_"):
            return ToolSource.SKILL
        elif name.startswith("mcp_"):
            return ToolSource.MCP
        return None

    def get_builtin_tool(self, name: str) -> Optional[Tool]:
        """Get a built-in tool."""
        return self._builtin_tools.get(name)

    def list_builtin_tools(self) -> Dict[str, Tool]:
        """List all built-in tools."""
        return dict(self._builtin_tools)
