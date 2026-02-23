from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


def compute_initial_next_run_at(
    task_type: str,
    schedule: Dict[str, Any],
    timezone_name: str,
    now_utc: Optional[datetime] = None,
) -> Optional[str]:
    return _compute_next_run(task_type, schedule, timezone_name, now_utc=now_utc, strict_future=False)


def compute_next_run_at(
    task_type: str,
    schedule: Dict[str, Any],
    timezone_name: str,
    now_utc: Optional[datetime] = None,
) -> Optional[str]:
    return _compute_next_run(task_type, schedule, timezone_name, now_utc=now_utc, strict_future=True)


def _compute_next_run(
    task_type: str,
    schedule: Dict[str, Any],
    timezone_name: str,
    *,
    now_utc: Optional[datetime],
    strict_future: bool,
) -> Optional[str]:
    now = now_utc or datetime.now(timezone.utc)
    tz = _resolve_timezone(timezone_name)
    now_local = now.astimezone(tz)
    task_kind = (task_type or "").strip().lower()
    if task_kind == "one_time":
        run_at = _parse_run_at(schedule.get("run_at"), tz)
        if not run_at:
            return None
        if strict_future and run_at <= now:
            return None
        return _to_utc_iso(run_at)
    if task_kind != "periodic":
        return None
    frequency = str(schedule.get("frequency", "daily")).strip().lower()
    if frequency == "hourly":
        minute = int(schedule.get("minute", 0))
        candidate = now_local.replace(minute=max(0, min(minute, 59)), second=0, microsecond=0)
        if strict_future and candidate <= now_local:
            candidate = candidate + timedelta(hours=1)
        elif not strict_future and candidate < now_local:
            candidate = candidate + timedelta(hours=1)
        return _to_utc_iso(candidate)
    hour, minute = _parse_time_hhmm(str(schedule.get("time", "09:00")))
    candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if frequency == "daily":
        if strict_future and candidate <= now_local:
            candidate = candidate + timedelta(days=1)
        elif not strict_future and candidate < now_local:
            candidate = candidate + timedelta(days=1)
        return _to_utc_iso(candidate)
    if frequency == "weekly":
        dow_raw = str(schedule.get("day_of_week", "mon")).strip().lower()
        target_dow = WEEKDAYS.get(dow_raw, 0)
        days_ahead = (target_dow - candidate.weekday()) % 7
        candidate = candidate + timedelta(days=days_ahead)
        if strict_future and candidate <= now_local:
            candidate = candidate + timedelta(days=7)
        elif not strict_future and candidate < now_local:
            candidate = candidate + timedelta(days=7)
        return _to_utc_iso(candidate)
    return None


def _resolve_timezone(name: str) -> ZoneInfo:
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _parse_run_at(value: Any, tz: ZoneInfo) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _parse_time_hhmm(value: str) -> tuple[int, int]:
    try:
        hour_str, minute_str = value.split(":", 1)
        hour = max(0, min(int(hour_str), 23))
        minute = max(0, min(int(minute_str), 59))
        return hour, minute
    except Exception:
        return 9, 0


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
