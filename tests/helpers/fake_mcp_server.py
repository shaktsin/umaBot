#!/usr/bin/env python3
"""Minimal fake MCP server for testing.

Speaks JSON-RPC 2.0 over stdio (newline-delimited).

Tools exposed:
  echo       — returns {"type": "text", "text": <message>}
  error_tool — returns a JSON-RPC error (tests MCPError handling)

Run by MCPRegistry as a subprocess.  Do NOT import umabot here.
"""

import json
import sys


def _respond(req_id, *, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue

    try:
        req = json.loads(raw)
    except json.JSONDecodeError:
        continue

    method = req.get("method", "")
    req_id = req.get("id")  # None for notifications
    params = req.get("params") or {}

    if method == "initialize":
        _respond(
            req_id,
            result={
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp-server", "version": "1.0.0"},
            },
        )

    elif method == "notifications/initialized":
        pass  # notification — no response

    elif method == "tools/list":
        _respond(
            req_id,
            result={
                "tools": [
                    {
                        "name": "echo",
                        "description": "Returns the message back as text",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {"type": "string", "description": "Text to echo"}
                            },
                            "required": ["message"],
                        },
                    },
                    {
                        "name": "error_tool",
                        "description": "Always returns a JSON-RPC error (for testing)",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                ]
            },
        )

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}

        if tool_name == "echo":
            _respond(
                req_id,
                result={"content": [{"type": "text", "text": arguments.get("message", "")}]},
            )
        elif tool_name == "error_tool":
            _respond(
                req_id,
                error={"code": -32000, "message": "Intentional test error from error_tool"},
            )
        else:
            _respond(
                req_id,
                error={"code": -32601, "message": f"Unknown tool: {tool_name}"},
            )

    else:
        # Unknown method — return method-not-found error (only for requests, not notifications)
        if req_id is not None:
            _respond(
                req_id,
                error={"code": -32601, "message": f"Method not found: {method}"},
            )
