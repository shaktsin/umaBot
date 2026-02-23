from __future__ import annotations

import logging
from typing import Dict, Any

from umabot.skills.runtime import SkillRuntime

from .registry import RISK_YELLOW, Tool, ToolRegistry, ToolResult

logger = logging.getLogger("umabot.tools.skill_tools")


def register_skill_tools(registry: ToolRegistry, *, runtime: SkillRuntime) -> None:
    registry.register(
        Tool(
            name="skills.run_script",
            schema={
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "script": {"type": "string"},
                    "input": {"type": "object"},
                },
                "required": ["skill", "script"],
                "additionalProperties": False,
            },
            handler=_make_run_script_handler(runtime),
            risk_level=RISK_YELLOW,
            description="Run a declared script from an installed skill in an isolated runtime.",
        )
    )


def _make_run_script_handler(runtime: SkillRuntime):
    async def handler(args: Dict[str, Any]) -> ToolResult:
        skill = str(args.get("skill", "")).strip()
        script = str(args.get("script", "")).strip()
        payload = args.get("input", {}) or {}
        logger.debug(
            "skills.run_script invoked skill=%s script=%s input_keys=%s",
            skill,
            script,
            sorted(list(payload.keys())) if isinstance(payload, dict) else [],
        )
        result = await runtime.run_script(skill_name=skill, script=script, payload=payload)
        logger.debug(
            "skills.run_script completed skill=%s script=%s ok=%s output_len=%s",
            skill,
            script,
            result.ok,
            len(result.output or ""),
        )
        return ToolResult(
            content=result.output,
            data={"ok": result.ok, "result": result.data or {}},
        )

    return handler
