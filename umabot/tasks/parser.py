from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class TaskDraft:
    name: str
    prompt: str
    task_type: str
    schedule: Dict[str, Any]
    timezone: str = "UTC"


def parse_control_task_request(text: str, timezone: str = "UTC") -> Optional[TaskDraft]:
    raw = (text or "").strip()
    lower = raw.lower()
    if not raw:
        return None

    if lower.startswith("task once "):
        return _parse_task_once(raw[len("task once "):], timezone)
    if lower.startswith("task daily "):
        return _parse_task_daily(raw[len("task daily "):], timezone)
    if lower.startswith("task weekly "):
        return _parse_task_weekly(raw[len("task weekly "):], timezone)

    if "remind me every day" in lower or "remind me daily" in lower:
        body = _strip_prefix_phrase(raw, ["remind me every day", "remind me daily"])
        schedule = {"frequency": "daily", "time": _extract_hhmm(raw) or "09:00"}
        prompt = _build_prompt(body)
        return TaskDraft(name=_default_name(prompt), prompt=prompt, task_type="periodic", schedule=schedule, timezone=timezone)

    if "remind me every week" in lower or "remind me weekly" in lower:
        body = _strip_prefix_phrase(raw, ["remind me every week", "remind me weekly"])
        day_of_week = _extract_day_of_week(raw) or "mon"
        schedule = {
            "frequency": "weekly",
            "day_of_week": day_of_week,
            "time": _extract_hhmm(raw) or "09:00",
        }
        prompt = _build_prompt(body)
        return TaskDraft(name=_default_name(prompt), prompt=prompt, task_type="periodic", schedule=schedule, timezone=timezone)

    return None


def _parse_task_once(raw: str, timezone: str) -> Optional[TaskDraft]:
    pattern = r"(?P<when>\S+)\s+(?P<prompt>.+)"
    match = re.match(pattern, raw.strip())
    if not match:
        return None
    run_at = match.group("when")
    prompt = _build_prompt(match.group("prompt"))
    return TaskDraft(
        name=_default_name(prompt),
        prompt=prompt,
        task_type="one_time",
        schedule={"run_at": run_at},
        timezone=timezone,
    )


def _parse_task_daily(raw: str, timezone: str) -> Optional[TaskDraft]:
    pattern = r"(?P<time>\d{1,2}:\d{2})\s+(?P<prompt>.+)"
    match = re.match(pattern, raw.strip())
    if not match:
        return None
    prompt = _build_prompt(match.group("prompt"))
    return TaskDraft(
        name=_default_name(prompt),
        prompt=prompt,
        task_type="periodic",
        schedule={"frequency": "daily", "time": match.group("time")},
        timezone=timezone,
    )


def _parse_task_weekly(raw: str, timezone: str) -> Optional[TaskDraft]:
    pattern = r"(?P<dow>[A-Za-z]+)\s+(?P<time>\d{1,2}:\d{2})\s+(?P<prompt>.+)"
    match = re.match(pattern, raw.strip())
    if not match:
        return None
    prompt = _build_prompt(match.group("prompt"))
    return TaskDraft(
        name=_default_name(prompt),
        prompt=prompt,
        task_type="periodic",
        schedule={
            "frequency": "weekly",
            "day_of_week": match.group("dow").lower(),
            "time": match.group("time"),
        },
        timezone=timezone,
    )


def _default_name(prompt: str) -> str:
    clean = " ".join(prompt.split())
    return clean[:60] if clean else "scheduled task"


def _build_prompt(body: str) -> str:
    text = body.strip(" .")
    if not text:
        text = "Review my tasks and provide a concise update."
    return text


def _extract_hhmm(text: str) -> Optional[str]:
    match = re.search(r"\b(\d{1,2}:\d{2})\b", text)
    if not match:
        return None
    return match.group(1)


def _extract_day_of_week(text: str) -> Optional[str]:
    match = re.search(
        r"\b(mon|monday|tue|tuesday|wed|wednesday|thu|thursday|fri|friday|sat|saturday|sun|sunday)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).lower()


def _strip_prefix_phrase(text: str, phrases: list[str]) -> str:
    lower = text.lower()
    for phrase in phrases:
        idx = lower.find(phrase)
        if idx >= 0:
            return text[idx + len(phrase):].strip(" ,.")
    return text
