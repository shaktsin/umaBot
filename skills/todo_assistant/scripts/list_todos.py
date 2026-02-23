#!/usr/bin/env python3
from __future__ import annotations

from common import emit_ok, load_request, normalize_status, read_todos, resolve_todo_file


def main() -> None:
    req_input, req_cfg = load_request()
    status = str(req_input.get("status", "")).strip().lower()
    limit_raw = req_input.get("limit", 20)
    try:
        limit = max(1, min(int(limit_raw), 200))
    except Exception:
        limit = 20

    path = resolve_todo_file(req_cfg)
    todos = read_todos(path)
    if status:
        status = normalize_status(status)
        todos = [item for item in todos if str(item.get("status", "")).lower() == status]

    todos = todos[-limit:]
    if not todos:
        emit_ok(
            "No todos found in your todo store. Add one by saying: 'add todo <title>'.",
            items=[],
            count=0,
            todo_file=str(path),
        )
        return

    lines = []
    for item in todos:
        line = (
            f"{item.get('id', '-')}: {item.get('title', '')} "
            f"[{item.get('status', 'open')}]"
        )
        if item.get("due"):
            line += f" due={item.get('due')}"
        lines.append(line)
    message = "Todos:\n" + "\n".join(lines)
    emit_ok(message, items=todos, count=len(todos), todo_file=str(path))


if __name__ == "__main__":
    main()
