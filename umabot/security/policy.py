"""Security policy engine for umabot.

Evaluated before every tool call to enforce per-connector and per-user
allow/deny rules and role-based access control.

Decision priority (highest wins):
  1. Connector-level deny list
  2. User-level role deny list
  3. Connector-level allow list
  4. User-level role allow list
  5. Default: allow (defer to existing PolicyEngine risk-level logic)
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("umabot.security.policy")

# Built-in role definitions (can be overridden via config)
_DEFAULT_ROLES: Dict[str, Dict[str, Any]] = {
    "admin": {
        "allow": ["*"],
        "deny": [],
        "require_approval_for_red": False,
        "require_approval_for_yellow": False,
    },
    "trusted": {
        "allow": ["*"],
        "deny": [],
        "require_approval_for_red": True,
        "require_approval_for_yellow": False,
    },
    "user": {
        "allow": ["*"],
        "deny": ["shell.*"],
        "require_approval_for_red": True,
        "require_approval_for_yellow": True,
    },
    "readonly": {
        "allow": ["gmail.list", "gmail.read", "gmail.search", "gcal.list_events", "gtasks.list", "skill.get_instructions"],
        "deny": ["shell.*", "gmail.send", "gcal.create_event", "gcal.update_event", "gcal.delete_event", "gtasks.create", "gtasks.delete"],
        "require_approval_for_red": True,
        "require_approval_for_yellow": True,
    },
}


@dataclass
class SecurityDecision:
    allowed: bool
    reason: Optional[str] = None
    # Override risk level for this call (e.g. admin bypasses RISK_RED approval)
    override_risk: Optional[str] = None


class SecurityPolicy:
    """Evaluate tool access based on config-driven roles and ACLs."""

    def __init__(self, security_config) -> None:
        self._cfg = security_config
        # Merge built-in roles with config overrides
        self._roles: Dict[str, Dict[str, Any]] = dict(_DEFAULT_ROLES)
        for role_name, role_data in (security_config.roles or {}).items():
            if isinstance(role_data, dict):
                self._roles[role_name] = {**_DEFAULT_ROLES.get(role_name, {}), **role_data}

    def evaluate(
        self,
        tool_name: str,
        *,
        user_id: str = "",
        connector: str = "",
    ) -> SecurityDecision:
        """Return SecurityDecision for the given tool + context.

        Called before PolicyEngine.evaluate() so security rules can
        short-circuit before risk-level confirmation logic runs.
        """
        if not getattr(self._cfg, "enabled", True):
            return SecurityDecision(allowed=True)

        role = self._resolve_role(user_id=user_id, connector=connector)
        role_cfg = self._roles.get(role, self._roles["user"])

        deny_patterns: List[str] = role_cfg.get("deny", [])
        allow_patterns: List[str] = role_cfg.get("allow", ["*"])

        # Deny takes precedence
        for pattern in deny_patterns:
            if fnmatch.fnmatch(tool_name, pattern):
                logger.info(
                    "Security deny tool=%s user=%s connector=%s role=%s pattern=%s",
                    tool_name, user_id, connector, role, pattern,
                )
                return SecurityDecision(
                    allowed=False,
                    reason=f"Tool '{tool_name}' is not permitted for your access level.",
                )

        # Check allow
        for pattern in allow_patterns:
            if fnmatch.fnmatch(tool_name, pattern):
                override = _risk_override(role_cfg)
                return SecurityDecision(allowed=True, override_risk=override)

        return SecurityDecision(
            allowed=False,
            reason=f"Tool '{tool_name}' is not in the allowed list for your access level.",
        )

    def _resolve_role(self, *, user_id: str, connector: str) -> str:
        """Return the role name for user_id in connector context."""
        # Per-user override
        user_map = getattr(self._cfg, "users", {}) or {}
        if user_id and user_id in user_map:
            entry = user_map[user_id]
            if isinstance(entry, dict):
                return entry.get("role", "user")
            return str(entry)

        # Per-connector default role
        conn_map = getattr(self._cfg, "connectors", {}) or {}
        if connector and connector in conn_map:
            entry = conn_map[connector]
            if isinstance(entry, dict):
                return entry.get("default_role", "user")

        return "user"


def _risk_override(role_cfg: Dict[str, Any]) -> Optional[str]:
    """Return GREEN if this role bypasses all approval requirements."""
    from umabot.tools.registry import RISK_GREEN
    no_red = not role_cfg.get("require_approval_for_red", True)
    no_yellow = not role_cfg.get("require_approval_for_yellow", False)
    if no_red and no_yellow:
        return RISK_GREEN
    return None
