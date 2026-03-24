"""Credential masking for tool output and logs.

Strips patterns that look like API keys, tokens, passwords from text
before it is stored in the audit log, passed to the LLM, or shown to users.
"""

from __future__ import annotations

import re
from typing import List

# Patterns that identify credential-like strings.
# Each pattern matches a key=value or header-style assignment.
_PATTERNS: List[re.Pattern] = [
    # Generic key=value / key: value patterns
    re.compile(
        r'(?i)(api[_\-]?key|secret[_\-]?key|client[_\-]?secret|access[_\-]?token'
        r'|refresh[_\-]?token|auth[_\-]?token|password|passwd|private[_\-]?key'
        r'|authorization)\s*[=:]\s*["\']?([A-Za-z0-9\-_./+]{8,})["\']?'
    ),
    # Bearer <token> (HTTP Authorization header)
    re.compile(r'(?i)\b(bearer)\s+([A-Za-z0-9\-_.~+/]{8,}={0,2})'),
    # AWS access keys
    re.compile(r'\b(AKIA[0-9A-Z]{16})\b'),
    # OpenAI / Anthropic style sk-…
    re.compile(r'\b(sk-[A-Za-z0-9]{20,})\b'),
    # Google / GCP tokens (ghp_, ghs_, gho_, etc.)
    re.compile(r'\b(gh[pousr]_[A-Za-z0-9]{10,})\b'),
    # Google API keys
    re.compile(r'\b(AIza[0-9A-Za-z_\-]{35})\b'),
    # Generic long hex/base64 tokens (40+ chars) that look like secrets
    re.compile(r'(?<![A-Za-z0-9])([A-Za-z0-9+/]{40,}={0,2})(?![A-Za-z0-9+/=])'),
]

_REDACTED = "[REDACTED]"


def mask_secrets(text: str) -> str:
    """Return *text* with credential-like values replaced by ``[REDACTED]``."""
    if not text:
        return text
    for pattern in _PATTERNS:
        # For patterns with 2 groups, preserve the key/prefix and redact only the value
        if pattern.groups == 2:
            text = pattern.sub(lambda m: f"{m.group(1)} {_REDACTED}" if " " in m.group(0) else f"{m.group(1)}={_REDACTED}", text)
        else:
            text = pattern.sub(_REDACTED, text)
    return text
