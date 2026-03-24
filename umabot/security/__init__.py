"""Security layer for umabot.

Provides:
  SecurityPolicy  — per-connector, per-user tool allow/deny rules evaluated before every tool call
  check_ssrf      — SSRF protection for web.fetch and similar tools
  mask_secrets    — credential masking applied to tool output before logging / LLM context
  get_user_role   — resolve user_id + connector to a named role
"""

from .masking import mask_secrets
from .policy import SecurityPolicy, SecurityDecision
from .ssrf import check_ssrf, SSRFError

__all__ = [
    "SecurityPolicy",
    "SecurityDecision",
    "check_ssrf",
    "SSRFError",
    "mask_secrets",
]
