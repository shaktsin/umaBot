from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


def load_request() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}, {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return {}, {}
    req_input = payload.get("input") if isinstance(payload.get("input"), dict) else {}
    req_cfg = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    return req_input, req_cfg


def resolve_todo_file(config: Dict[str, Any]) -> Path:
    todo_file = str(config.get("todo_file") or "~/.umabot/vault/todos.jsonl")
    path = Path(todo_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    return path


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return f"todo_{uuid.uuid4().hex[:10]}"


def read_todos(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                items.append(obj)
        except json.JSONDecodeError:
            continue
    return items


def write_todos(path: Path, todos: List[Dict[str, Any]]) -> None:
    lines = [json.dumps(item, ensure_ascii=True) for item in todos]
    content = "\n".join(lines)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def emit_ok(message: str, **data: Any) -> None:
    payload = {"ok": True, "message": message}
    payload.update(data)
    print(json.dumps(payload, ensure_ascii=True))


def emit_error(message: str, code: int = 1) -> None:
    print(json.dumps({"ok": False, "message": message}, ensure_ascii=True))
    sys.exit(code)


def normalize_status(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw in {"open", "done", "cancelled"}:
        return raw
    return "open"
