"""Unified tool registry managing built-in tools and MCP servers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from .registry import Tool, ToolResult

logger = logging.getLogger("umabot.tools.unified")


class ToolSource(Enum):
    """Source of a tool."""
    BUILTIN = "builtin"
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
    """Single registry for all tool types (built-in, MCP)."""

    def __init__(self):
        self._builtin_tools: Dict[str, Tool] = {}
        self._mcp_registry: Optional[Any] = None

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

        # 2. MCP tools
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

    async def execute_tool(self, name: str, arguments: dict) -> ToolResult:
        """Execute any tool by name."""
        # 1. Built-in tool?
        if name in self._builtin_tools:
            tool = self._builtin_tools[name]
            logger.debug(f"Executing built-in tool: {name}")
            result = await tool.handler(arguments)
            return result

        # 2. MCP tool?
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
        elif name.startswith("mcp_"):
            return ToolSource.MCP
        return None

    def get_builtin_tool(self, name: str) -> Optional[Tool]:
        """Get a built-in tool."""
        return self._builtin_tools.get(name)

    def list_builtin_tools(self) -> Dict[str, Tool]:
        """List all built-in tools."""
        return dict(self._builtin_tools)
