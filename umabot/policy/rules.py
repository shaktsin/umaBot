from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional

logger = logging.getLogger("umabot.policy.rules")

_TOOL_EFFECTS = {"inherit", "allow", "deny", "require_confirmation"}
_INTENT_ACTIONS = {"summarize", "draft_reply", "create_task", "ignore"}
_IMPORTANCE = {"high", "medium", "low"}
_DIRECTIONS = {"inbound", "outbound"}
_CONNECTOR_ROLES = {"listener", "admin"}


@dataclass
class RuleContext:
    connector: str = ""
    source_connector: str = ""
    connector_role: str = ""
    channel: str = ""
    direction: str = ""
    kind: str = ""
    action: str = ""
    importance: str = ""
    needs_admin: Optional[bool] = None
    admin_explicit: Optional[bool] = None


@dataclass
class ToolRuleDecision:
    effect: str = "inherit"  # inherit | allow | deny | require_confirmation
    reason: str = ""
    rule_id: str = ""


@dataclass
class IntentRuleDecision:
    ingest_to_llm: Optional[bool] = None
    set_action: str = ""
    set_importance: str = ""
    set_needs_admin: Optional[bool] = None
    reason: str = ""
    rule_id: str = ""


@dataclass
class _CompiledRule:
    id: str
    priority: int
    description: str
    connectors: List[str]
    connector_roles: List[str]
    channels: List[str]
    directions: List[str]
    kinds: List[str]
    tools: List[str]
    importance: List[str]
    actions: List[str]
    needs_admin: Optional[bool]
    admin_explicit: Optional[bool]
    tool_effect: str
    ingest_to_llm: Optional[bool]
    set_importance: str
    set_action: str
    set_needs_admin: Optional[bool]
    reason: str


class DeclarativePolicyEngine:
    """Rule engine for config-driven, connector-agnostic ACL decisions."""

    def __init__(self, rules: Optional[list]) -> None:
        self._rules: List[_CompiledRule] = self._compile_rules(rules or [])

    @property
    def has_rules(self) -> bool:
        return bool(self._rules)

    def filter_tools_for_context(
        self,
        tool_names: Iterable[str],
        context: RuleContext,
        *,
        drop_confirm_required: bool = False,
    ) -> List[str]:
        """Drop tools that are explicitly denied for this context."""
        allowed: List[str] = []
        for name in tool_names:
            decision = self.decide_tool(name, context)
            if decision.effect == "deny":
                logger.info(
                    "Declarative policy removed tool=%s rule=%s reason=%s",
                    name,
                    decision.rule_id,
                    decision.reason,
                )
                continue
            if drop_confirm_required and decision.effect == "require_confirmation":
                logger.info(
                    "Declarative policy removed confirm-required tool=%s rule=%s (orchestrator fail-closed)",
                    name,
                    decision.rule_id,
                )
                continue
            allowed.append(name)
        return allowed

    def decide_tool(self, tool_name: str, context: RuleContext) -> ToolRuleDecision:
        for rule in self._rules:
            if not self._matches(rule, context, tool_name=tool_name):
                continue
            if rule.tool_effect == "inherit":
                continue
            return ToolRuleDecision(
                effect=rule.tool_effect,
                reason=rule.reason,
                rule_id=rule.id,
            )
        return ToolRuleDecision()

    def decide_intent(self, context: RuleContext) -> IntentRuleDecision:
        for rule in self._rules:
            if not self._matches(rule, context, tool_name=None):
                continue
            if (
                rule.ingest_to_llm is None
                and not rule.set_action
                and not rule.set_importance
                and rule.set_needs_admin is None
            ):
                continue
            return IntentRuleDecision(
                ingest_to_llm=rule.ingest_to_llm,
                set_action=rule.set_action,
                set_importance=rule.set_importance,
                set_needs_admin=rule.set_needs_admin,
                reason=rule.reason,
                rule_id=rule.id,
            )
        return IntentRuleDecision()

    def _compile_rules(self, raw_rules: list) -> List[_CompiledRule]:
        compiled: List[_CompiledRule] = []
        for idx, rule in enumerate(raw_rules):
            if not getattr(rule, "enabled", True):
                continue

            rule_id = str(getattr(rule, "id", "") or f"rule-{idx + 1}")
            priority = int(getattr(rule, "priority", 100))
            description = str(getattr(rule, "description", "") or "")
            match = getattr(rule, "match", None)
            apply_cfg = getattr(rule, "apply", None)
            if not match or not apply_cfg:
                logger.warning("Skipping malformed policy rule id=%s (missing match/apply)", rule_id)
                continue

            tool_effect = str(getattr(apply_cfg, "tool", "inherit") or "inherit").strip().lower()
            if tool_effect not in _TOOL_EFFECTS:
                logger.warning(
                    "Skipping policy rule id=%s invalid apply.tool=%s",
                    rule_id,
                    tool_effect,
                )
                continue

            set_action = str(getattr(apply_cfg, "set_action", "") or "").strip().lower()
            if set_action and set_action not in _INTENT_ACTIONS:
                logger.warning(
                    "Skipping policy rule id=%s invalid apply.set_action=%s",
                    rule_id,
                    set_action,
                )
                continue

            set_importance = str(getattr(apply_cfg, "set_importance", "") or "").strip().lower()
            if set_importance and set_importance not in _IMPORTANCE:
                logger.warning(
                    "Skipping policy rule id=%s invalid apply.set_importance=%s",
                    rule_id,
                    set_importance,
                )
                continue

            compiled.append(
                _CompiledRule(
                    id=rule_id,
                    priority=priority,
                    description=description,
                    connectors=self._norm_patterns(getattr(match, "connectors", [])),
                    connector_roles=self._norm_enum_list(
                        getattr(match, "connector_roles", []), _CONNECTOR_ROLES
                    ),
                    channels=self._norm_patterns(getattr(match, "channels", [])),
                    directions=self._norm_enum_list(getattr(match, "directions", []), _DIRECTIONS),
                    kinds=self._norm_patterns(getattr(match, "kinds", [])),
                    tools=self._norm_patterns(getattr(match, "tools", [])),
                    importance=self._norm_enum_list(getattr(match, "importance", []), _IMPORTANCE),
                    actions=self._norm_enum_list(getattr(match, "actions", []), _INTENT_ACTIONS),
                    needs_admin=getattr(match, "needs_admin", None),
                    admin_explicit=getattr(match, "admin_explicit", None),
                    tool_effect=tool_effect,
                    ingest_to_llm=getattr(apply_cfg, "ingest_to_llm", None),
                    set_importance=set_importance,
                    set_action=set_action,
                    set_needs_admin=getattr(apply_cfg, "set_needs_admin", None),
                    reason=str(getattr(apply_cfg, "reason", "") or ""),
                )
            )

        compiled.sort(key=lambda item: item.priority)
        if compiled:
            logger.info("Declarative policy loaded rules=%d", len(compiled))
        return compiled

    def _matches(self, rule: _CompiledRule, context: RuleContext, *, tool_name: Optional[str]) -> bool:
        if rule.connectors:
            values = [context.source_connector, context.connector]
            if not any(self._match_any(value, rule.connectors) for value in values if value):
                return False
        if rule.connector_roles and not self._match_any(context.connector_role, rule.connector_roles):
            return False
        if rule.channels and not self._match_any(context.channel, rule.channels):
            return False
        if rule.directions and context.direction not in rule.directions:
            return False
        if rule.kinds and not self._match_any(context.kind, rule.kinds):
            return False
        if rule.tools:
            if not tool_name or not self._match_any(tool_name, rule.tools):
                return False
        if rule.importance:
            if context.importance not in rule.importance:
                return False
        if rule.actions:
            if context.action not in rule.actions:
                return False
        if rule.needs_admin is not None:
            if context.needs_admin is None or context.needs_admin != rule.needs_admin:
                return False
        if rule.admin_explicit is not None:
            if context.admin_explicit is None or context.admin_explicit != rule.admin_explicit:
                return False
        return True

    @staticmethod
    def _match_any(value: str, patterns: List[str]) -> bool:
        if not value:
            return False
        lowered = value.lower()
        return any(fnmatch.fnmatch(lowered, pattern) for pattern in patterns)

    @staticmethod
    def _norm_patterns(values: list) -> List[str]:
        normalized: List[str] = []
        for value in values or []:
            text = str(value).strip().lower()
            if text:
                normalized.append(text)
        return normalized

    @staticmethod
    def _norm_enum_list(values: list, allowed: set[str]) -> List[str]:
        normalized: List[str] = []
        for value in values or []:
            text = str(value).strip().lower()
            if not text:
                continue
            if text in allowed:
                normalized.append(text)
            else:
                logger.warning("Ignoring unknown policy enum value=%s allowed=%s", text, sorted(allowed))
        return normalized
