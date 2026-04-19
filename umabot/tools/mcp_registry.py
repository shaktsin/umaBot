"""MCPRegistry — manages MCP servers over stdio or HTTP and routes tool calls.

Two transports are supported:

**stdio** (default)
    umabot spawns the server as a child process.  Communication uses
    newline-delimited JSON-RPC 2.0 over stdin/stdout.  Used for local
    servers installed via npm/pip (e.g. ``npx @playwright/mcp@latest``).

**http**
    The server is already running externally (e.g. inside Docker).
    umabot connects over the network using the MCP Streamable HTTP
    transport: each JSON-RPC request is a POST to ``url`` and the
    response is either plain JSON or an SSE stream.

Tools from every server are namespaced as ``mcp_<server_name>_<tool_name>``
so they integrate cleanly with UnifiedToolRegistry, which routes any name
starting with ``"mcp_"`` here.

Typical lifecycle::

    registry = MCPRegistry(config.mcp_servers)
    await registry.start()                       # connect/spawn, discover tools
    unified_registry.set_mcp_registry(registry)  # plug into the tool pipeline
    # ... umabot runs ...
    await registry.stop()                        # graceful shutdown
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("umabot.tools.mcp")

_PROTOCOL_VERSION = "2024-11-05"
# Playwright screenshots can return large base64 payloads in a single JSON line.
# Raise asyncio StreamReader limit for stdio MCP subprocess pipes accordingly.
_STDIO_READ_LIMIT = 32 * 1024 * 1024  # 32 MiB


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MCPError(Exception):
    """Raised when an MCP server returns a JSON-RPC error or misbehaves."""


class MCPServerUnavailable(MCPError):
    """Raised when a tool is called but its backing server is not connected."""


# ---------------------------------------------------------------------------
# MCPRegistry
# ---------------------------------------------------------------------------

class MCPRegistry:
    """Manages one or more MCP servers (stdio or HTTP) and exposes their tools."""

    def __init__(self, servers: list) -> None:
        self._servers = servers

        # stdio transport state
        self._processes: Dict[str, asyncio.subprocess.Process] = {}

        # http transport state
        self._http_sessions: Dict[str, Any] = {}  # name -> aiohttp.ClientSession
        self._http_urls: Dict[str, str] = {}       # name -> base url

        # shared
        self._transport: Dict[str, str] = {}       # name -> "stdio" | "http"
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._request_id: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to / spawn all enabled servers and discover their tools."""
        for server in self._servers:
            if not getattr(server, "enabled", True):
                logger.info("MCP server '%s' disabled — skipping", server.name)
                continue
            try:
                transport = getattr(server, "transport", "stdio") or "stdio"
                if transport == "http":
                    await self._start_http_server(server)
                else:
                    await self._start_stdio_server(server)
            except Exception as exc:
                logger.error(
                    "Failed to connect MCP server '%s': %s", server.name, exc, exc_info=True
                )

    async def stop(self) -> None:
        """Shut down all servers gracefully."""
        # stdio: terminate child processes
        for name, proc in list(self._processes.items()):
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
                logger.info("MCP stdio server '%s' stopped", name)
            except asyncio.TimeoutError:
                logger.warning("MCP server '%s' did not exit — killing", name)
                proc.kill()
            except Exception as exc:
                logger.warning("Error stopping MCP server '%s': %s", name, exc)

        # http: close aiohttp sessions
        for name, session in list(self._http_sessions.items()):
            try:
                await session.close()
                logger.info("MCP http server '%s' disconnected", name)
            except Exception as exc:
                logger.warning("Error closing MCP http session '%s': %s", name, exc)

        self._processes.clear()
        self._http_sessions.clear()
        self._http_urls.clear()
        self._transport.clear()
        self._tools.clear()
        self._locks.clear()

    # ------------------------------------------------------------------
    # Internal — stdio startup
    # ------------------------------------------------------------------

    async def _start_stdio_server(self, server) -> None:
        cmd = [server.command] + list(getattr(server, "args", []) or [])
        extra_env = getattr(server, "env", {}) or {}
        env = {**os.environ, **extra_env}

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=_STDIO_READ_LIMIT,
        )
        self._processes[server.name] = proc
        self._transport[server.name] = "stdio"
        self._locks[server.name] = asyncio.Lock()
        logger.info("Started MCP stdio server '%s' (pid=%d)", server.name, proc.pid)

        await self._initialize(server.name)
        await self._discover_tools(server.name)

    # ------------------------------------------------------------------
    # Internal — http startup
    # ------------------------------------------------------------------

    async def _start_http_server(self, server) -> None:
        import aiohttp

        url = (getattr(server, "url", "") or "").rstrip("/")
        if not url:
            raise ValueError(f"MCP http server '{server.name}' has no url configured")

        session = aiohttp.ClientSession()
        self._http_sessions[server.name] = session
        self._http_urls[server.name] = url
        self._transport[server.name] = "http"
        self._locks[server.name] = asyncio.Lock()
        logger.info("Connecting to MCP http server '%s' at %s", server.name, url)

        await self._initialize(server.name)
        await self._discover_tools(server.name)

    # ------------------------------------------------------------------
    # Internal — stdio JSON-RPC primitives
    # ------------------------------------------------------------------

    async def _stdio_send(self, server_name: str, message: Dict[str, Any]) -> None:
        proc = self._processes[server_name]
        line = json.dumps(message, separators=(",", ":")) + "\n"
        proc.stdin.write(line.encode())
        await proc.stdin.drain()

    async def _stdio_recv(self, server_name: str, timeout: float) -> Dict[str, Any]:
        proc = self._processes[server_name]
        while True:
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            if not raw:
                raise MCPError(f"MCP server '{server_name}' closed stdout unexpectedly")
            text = raw.decode().strip()
            if text:
                return json.loads(text)

    async def _rpc_stdio(
        self, server_name: str, message: Dict[str, Any], timeout: float
    ) -> Any:
        req_id = message.get("id")
        await self._stdio_send(server_name, message)
        while True:
            resp = await self._stdio_recv(server_name, timeout)
            if resp.get("id") == req_id:
                if "error" in resp:
                    err = resp["error"]
                    raise MCPError(
                        f"MCP '{server_name}' error for '{message['method']}': "
                        f"{err.get('code')} — {err.get('message')}"
                    )
                return resp.get("result")
            if resp.get("id") is None:
                logger.debug("MCP '%s' notification: %s", server_name, resp.get("method"))

    async def _notify_stdio(self, server_name: str, method: str, params: Optional[Dict] = None) -> None:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        await self._stdio_send(server_name, msg)

    # ------------------------------------------------------------------
    # Internal — http JSON-RPC primitives
    # ------------------------------------------------------------------

    async def _rpc_http(
        self, server_name: str, message: Dict[str, Any], timeout: float
    ) -> Any:
        import aiohttp

        session = self._http_sessions[server_name]
        url = self._http_urls[server_name]
        req_id = message.get("id")

        async with session.post(
            url,
            json=message,
            headers={"Accept": "application/json, text/event-stream"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")

            if "text/event-stream" in content_type:
                # SSE stream — scan for the data line matching our request id
                result = await self._parse_sse_response(resp, req_id, server_name, timeout)
            else:
                # Plain JSON response
                data = await resp.json(content_type=None)
                if "error" in data:
                    err = data["error"]
                    raise MCPError(
                        f"MCP '{server_name}' error for '{message['method']}': "
                        f"{err.get('code')} — {err.get('message')}"
                    )
                result = data.get("result")

        return result

    async def _parse_sse_response(
        self, resp: Any, req_id: int, server_name: str, timeout: float
    ) -> Any:
        """Read an SSE stream until we find the JSON-RPC response matching req_id."""
        deadline = asyncio.get_event_loop().time() + timeout
        async for raw_line in resp.content:
            if asyncio.get_event_loop().time() > deadline:
                raise MCPError(f"MCP '{server_name}' SSE response timed out")
            line = raw_line.decode().rstrip("\r\n")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if data.get("id") != req_id:
                continue
            if "error" in data:
                err = data["error"]
                raise MCPError(
                    f"MCP '{server_name}' error: {err.get('code')} — {err.get('message')}"
                )
            return data.get("result")
        raise MCPError(f"MCP '{server_name}' SSE stream ended without a response for id={req_id}")

    async def _notify_http(self, server_name: str, method: str, params: Optional[Dict] = None) -> None:
        """Send a JSON-RPC notification (no id) over HTTP; expect 202 or ignore body."""
        import aiohttp

        session = self._http_sessions[server_name]
        url = self._http_urls[server_name]
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        try:
            async with session.post(
                url, json=msg, timeout=aiohttp.ClientTimeout(total=10.0)
            ) as resp:
                pass  # 202 Accepted or similar — we don't need the body
        except Exception as exc:
            logger.debug("MCP '%s' notification '%s' error (ignored): %s", server_name, method, exc)

    # ------------------------------------------------------------------
    # Internal — unified RPC dispatcher
    # ------------------------------------------------------------------

    async def _rpc(
        self,
        server_name: str,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> Any:
        async with self._locks[server_name]:
            self._request_id += 1
            message: Dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
            }
            if params is not None:
                message["params"] = params

            if self._transport[server_name] == "http":
                return await self._rpc_http(server_name, message, timeout)
            else:
                return await self._rpc_stdio(server_name, message, timeout)

    async def _notify(self, server_name: str, method: str, params: Optional[Dict] = None) -> None:
        if self._transport[server_name] == "http":
            await self._notify_http(server_name, method, params)
        else:
            await self._notify_stdio(server_name, method, params)

    # ------------------------------------------------------------------
    # Internal — MCP handshake + tool discovery (transport-agnostic)
    # ------------------------------------------------------------------

    async def _initialize(self, server_name: str) -> None:
        result = await self._rpc(
            server_name,
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "umabot", "version": "1.0.0"},
            },
            timeout=15.0,
        )
        info = (result or {}).get("serverInfo", {})
        logger.info(
            "MCP '%s' handshake ok — server: %s %s",
            server_name, info.get("name", "?"), info.get("version", ""),
        )
        await self._notify(server_name, "notifications/initialized")

    async def _discover_tools(self, server_name: str) -> None:
        result = await self._rpc(server_name, "tools/list", {})
        tools: List[Dict] = (result or {}).get("tools", [])
        for tool in tools:
            original_name = tool["name"]
            prefixed = f"mcp_{server_name}_{original_name}"
            self._tools[prefixed] = {
                "server": server_name,
                "tool": original_name,
                "description": tool.get("description", ""),
                "schema": tool.get("inputSchema", {"type": "object", "properties": {}}),
            }
        logger.info(
            "MCP '%s': discovered %d tool(s): %s",
            server_name, len(tools), [t["name"] for t in tools],
        )

    # ------------------------------------------------------------------
    # Public interface (consumed by UnifiedToolRegistry)
    # ------------------------------------------------------------------

    def get_all_tools(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._tools)

    async def call_tool(self, prefixed_name: str, arguments: Dict[str, Any]) -> List[Dict]:
        """Execute a tool and return the MCP content list."""
        if prefixed_name not in self._tools:
            raise ValueError(f"Unknown MCP tool: {prefixed_name}")

        tool_info = self._tools[prefixed_name]
        server_name = tool_info["server"]

        # Availability check — works for both transports
        transport = self._transport.get(server_name)
        if transport == "http" and server_name not in self._http_sessions:
            raise MCPServerUnavailable(
                f"MCP http server '{server_name}' is not connected (tool: {prefixed_name})"
            )
        if transport == "stdio" and server_name not in self._processes:
            raise MCPServerUnavailable(
                f"MCP stdio server '{server_name}' is not running (tool: {prefixed_name})"
            )
        if transport is None:
            raise MCPServerUnavailable(
                f"MCP server '{server_name}' is not available (tool: {prefixed_name})"
            )

        result = await self._rpc(
            server_name,
            "tools/call",
            {"name": tool_info["tool"], "arguments": arguments},
            timeout=60.0,
        )
        return (result or {}).get("content", [])
