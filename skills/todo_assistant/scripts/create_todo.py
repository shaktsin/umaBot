#!/usr/bin/env python3
from __future__ import annotations

from common import emit_error, emit_ok, load_request, new_id, now_utc, read_todos, resolve_todo_file, write_todos


def main() -> None:
    req_input, req_cfg = load_request()
    title = str(req_input.get("title", "")).strip()
    if not title:
        emit_error("Missing required field: title")
    due = str(req_input.get("due", "")).strip()
    notes = str(req_input.get("notes", "")).strip()

    path = resolve_todo_file(req_cfg)
    todos = read_todos(path)
    item = {
        "id": new_id(),
        "title": title,
        "status": "open",
        "due": due,
        "notes": notes,
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    todos.append(item)
    write_todos(path, todos)
    emit_ok(
        f"Created todo '{title}' ({item['id']})",
        item=item,
        todo_file=str(path),
    )


if __name__ == "__main__":
    main()
