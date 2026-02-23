#!/usr/bin/env python3
from __future__ import annotations

from common import emit_error, emit_ok, load_request, normalize_status, now_utc, read_todos, resolve_todo_file, write_todos


def main() -> None:
    req_input, req_cfg = load_request()
    todo_id = str(req_input.get("id", "")).strip()
    if not todo_id:
        emit_error("Missing required field: id")

    path = resolve_todo_file(req_cfg)
    todos = read_todos(path)
    found = None
    for item in todos:
        if str(item.get("id", "")).strip() == todo_id:
            found = item
            break
    if not found:
        emit_error(f"Todo not found: {todo_id}")

    if "title" in req_input:
        found["title"] = str(req_input.get("title", "")).strip()
    if "due" in req_input:
        found["due"] = str(req_input.get("due", "")).strip()
    if "notes" in req_input:
        found["notes"] = str(req_input.get("notes", "")).strip()
    if "status" in req_input:
        found["status"] = normalize_status(str(req_input.get("status", "")))

    found["updated_at"] = now_utc()
    write_todos(path, todos)
    emit_ok(f"Updated todo {todo_id}", item=found, todo_file=str(path))


if __name__ == "__main__":
    main()
