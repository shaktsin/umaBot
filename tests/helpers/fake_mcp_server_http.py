"""Fake MCP HTTP server for testing.

Implements the MCP Streamable HTTP transport.
Supports both plain JSON responses and SSE responses (controlled by Accept header).

Tools exposed (same as fake_mcp_server.py for parity):
  echo       — returns {"type": "text", "text": <message>}
  error_tool — returns a JSON-RPC error

Usage in tests::

    from tests.helpers.fake_mcp_server_http import start_fake_http_server

    async with start_fake_http_server() as url:
        # url is e.g. "http://127.0.0.1:54321"
        ...
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

from aiohttp import web


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

async def _handle(request: web.Request) -> web.StreamResponse:
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Bad JSON")

    method = body.get("method", "")
    req_id = body.get("id")           # None for notifications
    params = body.get("params") or {}

    # Notification — no response body needed
    if req_id is None:
        return web.Response(status=202)

    result = _dispatch(method, params, req_id)
    response_obj = {"jsonrpc": "2.0", "id": req_id}
    if isinstance(result, dict) and "__error__" in result:
        response_obj["error"] = result["__error__"]
    else:
        response_obj["result"] = result

    accept = request.headers.get("Accept", "")

    if "text/event-stream" in accept:
        # SSE response
        sse_resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
        )
        await sse_resp.prepare(request)
        payload = json.dumps(response_obj)
        await sse_resp.write(f"data: {payload}\n\n".encode())
        await sse_resp.write_eof()
        return sse_resp
    else:
        # Plain JSON response
        return web.json_response(response_obj)


def _dispatch(method: str, params: dict, req_id: int) -> object:
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-http-mcp-server", "version": "1.0.0"},
        }

    if method == "tools/list":
        return {
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
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if tool_name == "echo":
            return {"content": [{"type": "text", "text": arguments.get("message", "")}]}
        if tool_name == "error_tool":
            return {"__error__": {"code": -32000, "message": "Intentional test error from error_tool"}}
        return {"__error__": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}

    return {"__error__": {"code": -32601, "message": f"Method not found: {method}"}}


# ---------------------------------------------------------------------------
# Server lifecycle helper
# ---------------------------------------------------------------------------

@asynccontextmanager
async def start_fake_http_server() -> AsyncIterator[str]:
    """Start the fake HTTP MCP server on a random port and yield its base URL.

    Usage::

        async with start_fake_http_server() as url:
            cfg = MCPServerConfig(name="test", transport="http", url=url)
    """
    app = web.Application()
    app.router.add_post("/", _handle)

    runner = web.AppRunner(app)
    await runner.setup()

    # port=0 → OS assigns a free port
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()

    # Retrieve the assigned port
    sockets = site._server.sockets  # type: ignore[union-attr]
    port = sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"

    try:
        yield url
    finally:
        await runner.cleanup()
