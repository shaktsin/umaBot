"""Lightweight intent detection for inbound listener-connector messages.

Runs a single P2 LLM call before the main agent loop to classify:
  - importance   : high | medium | low
  - needs_admin  : whether human attention is required
  - suggested_action : summarize | draft_reply | create_task | ignore
  - summary      : 1-2 sentence description of what arrived

The result is used by the worker to:
  1. Short-circuit low-importance "ignore" messages without calling the LLM.
  2. Prepend an intent context block to the main LLM conversation so the
     agent knows what action is expected without re-reading the full message.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from umabot.llm.scheduler import LLMScheduler

logger = logging.getLogger("umabot.intent")

_SYSTEM_PROMPT = """\
You are a message classifier for a personal AI assistant.
Analyse the message below and respond with ONLY a JSON object — no extra text.

JSON schema:
{
  "importance": "high" | "medium" | "low",
  "needs_admin": true | false,
  "suggested_action": "summarize" | "draft_reply" | "create_task" | "ignore",
  "summary": "<1-2 sentence plain-English summary>"
}

Rules:
  importance "high"   — urgent: payment due, security alert, personal emergency, deadline today
  importance "medium" — relevant but not urgent: notification, update, request, invite
  importance "low"    — noise: marketing, automated system ping, newsletter, spam

  needs_admin true    — admin should see this and may need to decide or reply
  needs_admin false   — safe to handle automatically or ignore

  suggested_action:
    summarize    — inform admin what arrived, no reply needed
    draft_reply  — compose a reply for admin to review before sending
    create_task  — schedule a follow-up action
    ignore       — nothing to do; skip entirely\
"""


@dataclass
class IntentResult:
    importance: str = "medium"            # "high" | "medium" | "low"
    needs_admin: bool = True
    suggested_action: str = "summarize"   # "summarize" | "draft_reply" | "create_task" | "ignore"
    summary: str = ""

    @property
    def should_skip(self) -> bool:
        """True when the message can be silently discarded."""
        return self.suggested_action == "ignore" and self.importance == "low"


_FALLBACK = IntentResult(
    importance="medium",
    needs_admin=True,
    suggested_action="summarize",
    summary="(intent detection failed — treating as medium importance)",
)


async def detect_intent(text: str, llm_client: "LLMScheduler") -> IntentResult:
    """Run a lightweight P2 LLM call to classify *text*.

    Returns ``_FALLBACK`` on any error so the caller can always proceed safely.
    """
    from umabot.llm.scheduler import P2

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]

    try:
        response = await llm_client.generate(messages, tools=None, priority=P2)
        raw = (response.content or "").strip()

        # Strip markdown code fences if the model wraps the JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        data = json.loads(raw)
        return IntentResult(
            importance=str(data.get("importance", "medium")),
            needs_admin=bool(data.get("needs_admin", True)),
            suggested_action=str(data.get("suggested_action", "summarize")),
            summary=str(data.get("summary", "")),
        )
    except Exception as exc:
        logger.warning("Intent detection failed: %s — using fallback", exc)
        return _FALLBACK


# Maps connector type prefix → tool hint shown to the LLM
_REPLY_TOOL_HINTS: dict = {
    "gmail": "use the gmail.send tool (include the original Subject as Re: <Subject>)",
    "telegram_user": "use the telegram.send_message tool with the source_chat_id",
    "discord": "use the discord.send tool with the source_chat_id as channel_id",
}


def _reply_hint(source_connector: str) -> str:
    """Return a short tool hint for the given connector name."""
    for prefix, hint in _REPLY_TOOL_HINTS.items():
        if prefix in source_connector:
            return hint
    return "reply via the appropriate tool for this connector"


def intent_context_block(
    intent: IntentResult,
    source_connector: str = "",
    source_chat_id: str = "",
) -> str:
    """Return a short system-message block to prepend to the LLM conversation.

    Tells the agent what has already been determined about this message so it
    doesn't need to re-derive it.  When source_connector is provided, a reply
    routing hint is appended so the LLM knows which tool to call.
    """
    lines = [
        "[Intent detection]",
        f"Importance:        {intent.importance}",
        f"Needs admin:       {intent.needs_admin}",
        f"Suggested action:  {intent.suggested_action}",
        f"Summary:           {intent.summary}",
    ]
    if source_connector:
        lines.append(f"Source connector:  {source_connector}")
    if source_chat_id:
        lines.append(f"Source chat_id:    {source_chat_id}")
    lines.append("")
    lines.append("Act according to the suggested action above.")
    if source_connector and intent.suggested_action == "draft_reply":
        lines.append(f"To reply: {_reply_hint(source_connector)}")
    return "\n".join(lines)
