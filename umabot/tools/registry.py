from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from jsonschema import validate
from jsonschema.exceptions import ValidationError


RiskLevel = str

RISK_GREEN: RiskLevel = "GREEN"
RISK_YELLOW: RiskLevel = "YELLOW"
RISK_RED: RiskLevel = "RED"


@dataclass
class Tool:
    name: str
    schema: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Awaitable["ToolResult"]]
    risk_level: RiskLevel = RISK_GREEN
    description: Optional[str] = None


@dataclass
class ToolResult:
    content: str
    data: Optional[Dict[str, Any]] = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list(self) -> Dict[str, Tool]:
        return dict(self._tools)

    def validate_args(self, name: str, args: Dict[str, Any]) -> None:
        tool = self._tools.get(name)
        if not tool:
            raise ValidationError(f"Unknown tool: {name}")
        validate(instance=args, schema=tool.schema)
