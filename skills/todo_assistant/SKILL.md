---
name: todo_assistant
version: 1.0.0
description: Create, list, and update todos persisted in a JSONL file.
allowed_tools:
  - skills.run_script
risk_level: yellow
triggers:
  - "todo"
  - "todos"
  - "add todo"
  - "create todo"
  - "list todos"
  - "show my todos"
  - "complete todo"
scripts:
  create:
    path: scripts/create_todo.py
    description: Create a new todo item.
    input_schema:
      type: object
      properties:
        title:
          type: string
          minLength: 1
        due:
          type: string
        notes:
          type: string
      required: ["title"]
      additionalProperties: false
    arg_mapping:
      title: ["title", "task", "todo", "text", "message"]
    examples:
      - input:
          title: "Buy groceries"
          due: "2026-02-24"
          notes: "Milk and eggs"
  list:
    path: scripts/list_todos.py
    description: List todo items.
    input_schema:
      type: object
      properties:
        status:
          type: string
          enum: ["open", "done", "cancelled"]
        limit:
          type: integer
          minimum: 1
          maximum: 200
      additionalProperties: false
    examples:
      - input:
          status: "open"
          limit: 20
  update:
    path: scripts/update_todo.py
    description: Update an existing todo by id.
    input_schema:
      type: object
      properties:
        id:
          type: string
          minLength: 1
        title:
          type: string
        due:
          type: string
        notes:
          type: string
        status:
          type: string
          enum: ["open", "done", "cancelled"]
      required: ["id"]
      additionalProperties: false
    examples:
      - input:
          id: "todo_abc123"
          status: "done"
install_config:
  args:
    todo_file:
      type: string
      required: true
      default: "~/.umabot/vault/todos.jsonl"
  env:
    TODO_TIMEZONE:
      required: false
      secret: false
      default: "UTC"
runtime:
  timeout_seconds: 20
---

# Todo Assistant

This skill stores todos in a JSONL file configured during install (`todo_file`).

Use `skills.run_script` with:
- `skill`: `todo_assistant`
- `script`: one of `create`, `list`, `update`
- `input`: script-specific payload

## Script Inputs

### create
```json
{
  "title": "Buy groceries",
  "due": "2026-02-24",
  "notes": "Milk and eggs"
}
```

### list
```json
{
  "status": "open",
  "limit": 20
}
```

### update
```json
{
  "id": "todo_abc123",
  "status": "done"
}
```
