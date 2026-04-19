"""Tests for MCPRegistry — MCP server lifecycle, JSON-RPC protocol, and tool routing.

Strategy
--------
These are *integration-style* unit tests: we actually spawn/start the fake servers
so the full wire protocol is exercised, not mocked.

stdio fake server : tests/helpers/fake_mcp_server.py      (subprocess, JSON-RPC over stdio)
http  fake server : tests/helpers/fake_mcp_server_http.py (aiohttp, Streamable HTTP transport)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from umabot.config.schema import MCPServerConfig, Config
from umabot.config.loader import load_config
from umabot.tools.mcp_registry import MCPError, MCPRegistry, MCPServerUnavailable
from umabot.tools.unified_registry import ToolSource, UnifiedToolRegistry

FAKE_SERVER = str(Path(__file__).parent / "helpers" / "fake_mcp_server.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_server_config(name: str = "fake", enabled: bool = True) -> MCPServerConfig:
    """Return a MCPServerConfig that spawns the local fake MCP server."""
    return MCPServerConfig(
        name=name,
        command=sys.executable,
        args=[FAKE_SERVER],
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# 1. Config schema
# ---------------------------------------------------------------------------

class TestMCPServerConfig:
    def test_defaults(self):
        cfg = MCPServerConfig()
        assert cfg.name == ""
        assert cfg.command == ""
        assert cfg.args == []
        assert cfg.env == {}
        assert cfg.enabled is True

    def test_explicit_values(self):
        cfg = MCPServerConfig(
            name="playwright",
            command="npx",
            args=["@playwright/mcp@latest", "--headless"],
            env={"DEBUG": "1"},
            enabled=False,
        )
        assert cfg.name == "playwright"
        assert cfg.args == ["@playwright/mcp@latest", "--headless"]
        assert cfg.env == {"DEBUG": "1"}
        assert cfg.enabled is False

    def test_config_has_mcp_servers_field(self):
        cfg = Config()
        assert hasattr(cfg, "mcp_servers")
        assert cfg.mcp_servers == []

    def test_loader_parses_mcp_servers(self, tmp_path):
        import yaml

        data = {
            "mcp_servers": [
                {"name": "playwright", "command": "npx", "args": ["@playwright/mcp@latest"]},
                {"name": "github", "command": "npx", "enabled": False},
            ]
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(data))

        cfg, _ = load_config(config_path=str(cfg_file))

        assert len(cfg.mcp_servers) == 2
        assert cfg.mcp_servers[0].name == "playwright"
        assert cfg.mcp_servers[0].args == ["@playwright/mcp@latest"]
        assert cfg.mcp_servers[1].name == "github"
        assert cfg.mcp_servers[1].enabled is False


# ---------------------------------------------------------------------------
# 2. Lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestMCPRegistryLifecycle:
    async def test_start_with_no_servers_is_noop(self):
        reg = MCPRegistry([])
        await reg.start()  # should not raise
        assert reg.get_all_tools() == {}
        await reg.stop()

    async def test_start_discovers_tools(self):
        reg = MCPRegistry([_fake_server_config()])
        await reg.start()
        tools = reg.get_all_tools()
        assert "mcp_fake_echo" in tools
        assert "mcp_fake_error_tool" in tools
        await reg.stop()

    async def test_stop_clears_state(self):
        reg = MCPRegistry([_fake_server_config()])
        await reg.start()
        assert len(reg.get_all_tools()) == 2

        await reg.stop()
        assert reg.get_all_tools() == {}

    async def test_disabled_server_is_skipped(self):
        reg = MCPRegistry([_fake_server_config(enabled=False)])
        await reg.start()
        assert reg.get_all_tools() == {}
        await reg.stop()

    async def test_multiple_servers_namespaced(self):
        reg = MCPRegistry([
            _fake_server_config(name="alpha"),
            _fake_server_config(name="beta"),
        ])
        await reg.start()
        tools = reg.get_all_tools()
        # Each server contributes 2 tools under its own namespace
        assert "mcp_alpha_echo" in tools
        assert "mcp_beta_echo" in tools
        assert "mcp_alpha_error_tool" in tools
        assert "mcp_beta_error_tool" in tools
        await reg.stop()

    async def test_bad_command_does_not_crash_start(self):
        """A server that fails to start should be skipped, not abort startup."""
        bad = MCPServerConfig(name="bad", command="no_such_binary_xyz", args=[])
        good = _fake_server_config(name="good")
        reg = MCPRegistry([bad, good])
        await reg.start()  # must not raise
        tools = reg.get_all_tools()
        # good server still started
        assert "mcp_good_echo" in tools
        await reg.stop()


# ---------------------------------------------------------------------------
# 3. Tool metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestToolMetadata:
    async def test_tool_info_fields(self):
        reg = MCPRegistry([_fake_server_config()])
        await reg.start()

        tools = reg.get_all_tools()
        echo = tools["mcp_fake_echo"]

        assert echo["server"] == "fake"
        assert echo["tool"] == "echo"
        assert "echo" in echo["description"].lower() or echo["description"] != ""
        assert echo["schema"]["type"] == "object"
        assert "message" in echo["schema"]["properties"]

        await reg.stop()

    async def test_prefixed_name_format(self):
        reg = MCPRegistry([_fake_server_config(name="myserver")])
        await reg.start()
        names = list(reg.get_all_tools().keys())
        for name in names:
            assert name.startswith("mcp_myserver_"), f"Unexpected tool name: {name}"
        await reg.stop()


# ---------------------------------------------------------------------------
# 4. Tool execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestToolExecution:
    async def test_echo_tool_returns_text(self):
        reg = MCPRegistry([_fake_server_config()])
        await reg.start()

        content = await reg.call_tool("mcp_fake_echo", {"message": "hello world"})

        assert len(content) == 1
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "hello world"

        await reg.stop()

    async def test_echo_tool_empty_message(self):
        reg = MCPRegistry([_fake_server_config()])
        await reg.start()
        content = await reg.call_tool("mcp_fake_echo", {"message": ""})
        assert content[0]["text"] == ""
        await reg.stop()

    async def test_error_tool_raises_mcp_error(self):
        reg = MCPRegistry([_fake_server_config()])
        await reg.start()

        with pytest.raises(MCPError, match="Intentional test error"):
            await reg.call_tool("mcp_fake_error_tool", {})

        await reg.stop()

    async def test_unknown_tool_raises_value_error(self):
        reg = MCPRegistry([_fake_server_config()])
        await reg.start()

        with pytest.raises(ValueError, match="Unknown MCP tool"):
            await reg.call_tool("mcp_fake_does_not_exist", {})

        await reg.stop()

    async def test_call_after_stop_raises_unavailable(self):
        reg = MCPRegistry([_fake_server_config()])
        await reg.start()
        # Manually clear the process map to simulate a stopped server
        # while keeping _tools populated (edge case: stop between discovery and call)
        reg._processes.clear()

        with pytest.raises(MCPServerUnavailable):
            await reg.call_tool("mcp_fake_echo", {"message": "hi"})

        await reg.stop()

    async def test_sequential_calls_same_server(self):
        """Lock ensures sequential calls don't interleave messages."""
        reg = MCPRegistry([_fake_server_config()])
        await reg.start()

        results = await asyncio.gather(
            reg.call_tool("mcp_fake_echo", {"message": "first"}),
            reg.call_tool("mcp_fake_echo", {"message": "second"}),
            reg.call_tool("mcp_fake_echo", {"message": "third"}),
        )

        texts = {r[0]["text"] for r in results}
        assert texts == {"first", "second", "third"}

        await reg.stop()


# ---------------------------------------------------------------------------
# 5. UnifiedToolRegistry integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestUnifiedRegistryIntegration:
    async def test_mcp_tools_appear_in_get_all_tools(self):
        reg = MCPRegistry([_fake_server_config()])
        await reg.start()

        unified = UnifiedToolRegistry()
        unified.set_mcp_registry(reg)

        all_tools = unified.get_all_tools()
        assert "mcp_fake_echo" in all_tools
        assert all_tools["mcp_fake_echo"].source == ToolSource.MCP

        await reg.stop()

    async def test_unified_execute_mcp_tool(self):
        reg = MCPRegistry([_fake_server_config()])
        await reg.start()

        unified = UnifiedToolRegistry()
        unified.set_mcp_registry(reg)

        result = await unified.execute_tool("mcp_fake_echo", {"message": "via unified"})
        assert "via unified" in result.content

        await reg.stop()

    async def test_builtin_and_mcp_tools_coexist(self):
        from umabot.tools.registry import Tool, ToolResult, RISK_YELLOW

        reg = MCPRegistry([_fake_server_config()])
        await reg.start()

        unified = UnifiedToolRegistry()

        async def _noop(args):
            return ToolResult(content="noop")

        unified.register_builtin(Tool(
            name="builtin.noop",
            description="No-op test tool",
            schema={"type": "object", "properties": {}},
            handler=_noop,
            risk_level=RISK_YELLOW,
        ))
        unified.set_mcp_registry(reg)

        all_tools = unified.get_all_tools()
        assert "builtin.noop" in all_tools
        assert "mcp_fake_echo" in all_tools
        assert all_tools["builtin.noop"].source == ToolSource.BUILTIN
        assert all_tools["mcp_fake_echo"].source == ToolSource.MCP

        await reg.stop()

    async def test_no_mcp_registry_returns_only_builtins(self):
        unified = UnifiedToolRegistry()
        # No set_mcp_registry call
        tools = unified.get_all_tools()
        # Should not raise, just return empty (no builtins registered either)
        assert isinstance(tools, dict)


# ---------------------------------------------------------------------------
# 6. Config schema — http transport fields
# ---------------------------------------------------------------------------

class TestMCPServerConfigHttp:
    def test_transport_default_is_stdio(self):
        cfg = MCPServerConfig()
        assert cfg.transport == "stdio"

    def test_http_transport_fields(self):
        cfg = MCPServerConfig(
            name="docker_mcp",
            transport="http",
            url="http://localhost:8080",
        )
        assert cfg.transport == "http"
        assert cfg.url == "http://localhost:8080"
        assert cfg.command == ""   # not needed for http

    def test_loader_parses_http_transport(self, tmp_path):
        import yaml

        data = {
            "mcp_servers": [
                {"name": "local", "transport": "stdio", "command": "npx", "args": ["some-mcp"]},
                {"name": "docker", "transport": "http", "url": "http://localhost:9000"},
            ]
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(data))

        cfg, _ = load_config(config_path=str(cfg_file))

        assert cfg.mcp_servers[0].transport == "stdio"
        assert cfg.mcp_servers[1].transport == "http"
        assert cfg.mcp_servers[1].url == "http://localhost:9000"


# ---------------------------------------------------------------------------
# 7. HTTP transport — lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestHttpTransportLifecycle:
    async def test_start_discovers_tools_over_http(self):
        from tests.helpers.fake_mcp_server_http import start_fake_http_server

        async with start_fake_http_server() as url:
            cfg = MCPServerConfig(name="docker", transport="http", url=url)
            reg = MCPRegistry([cfg])
            await reg.start()

            tools = reg.get_all_tools()
            assert "mcp_docker_echo" in tools
            assert "mcp_docker_error_tool" in tools

            await reg.stop()

    async def test_stop_closes_http_session(self):
        from tests.helpers.fake_mcp_server_http import start_fake_http_server

        async with start_fake_http_server() as url:
            cfg = MCPServerConfig(name="docker", transport="http", url=url)
            reg = MCPRegistry([cfg])
            await reg.start()
            await reg.stop()

            assert reg.get_all_tools() == {}
            assert reg._http_sessions == {}

    async def test_unreachable_http_server_does_not_crash_start(self):
        bad = MCPServerConfig(name="gone", transport="http", url="http://127.0.0.1:1")
        good_cfg = _fake_server_config(name="stdio_good")
        reg = MCPRegistry([bad, good_cfg])
        await reg.start()  # must not raise

        tools = reg.get_all_tools()
        assert "mcp_stdio_good_echo" in tools   # stdio server still started
        assert "mcp_gone_echo" not in tools     # http server was skipped

        await reg.stop()

    async def test_disabled_http_server_skipped(self):
        from tests.helpers.fake_mcp_server_http import start_fake_http_server

        async with start_fake_http_server() as url:
            cfg = MCPServerConfig(name="docker", transport="http", url=url, enabled=False)
            reg = MCPRegistry([cfg])
            await reg.start()
            assert reg.get_all_tools() == {}
            await reg.stop()


# ---------------------------------------------------------------------------
# 8. HTTP transport — tool execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestHttpTransportExecution:
    async def test_echo_tool_json_response(self):
        from tests.helpers.fake_mcp_server_http import start_fake_http_server

        async with start_fake_http_server() as url:
            cfg = MCPServerConfig(name="docker", transport="http", url=url)
            reg = MCPRegistry([cfg])
            await reg.start()

            content = await reg.call_tool("mcp_docker_echo", {"message": "hello http"})

            assert content[0]["type"] == "text"
            assert content[0]["text"] == "hello http"

            await reg.stop()

    async def test_error_tool_raises_mcp_error(self):
        from tests.helpers.fake_mcp_server_http import start_fake_http_server

        async with start_fake_http_server() as url:
            cfg = MCPServerConfig(name="docker", transport="http", url=url)
            reg = MCPRegistry([cfg])
            await reg.start()

            with pytest.raises(MCPError, match="Intentional test error"):
                await reg.call_tool("mcp_docker_error_tool", {})

            await reg.stop()

    async def test_call_unavailable_http_server_raises(self):
        from tests.helpers.fake_mcp_server_http import start_fake_http_server

        async with start_fake_http_server() as url:
            cfg = MCPServerConfig(name="docker", transport="http", url=url)
            reg = MCPRegistry([cfg])
            await reg.start()
            # Simulate disconnected session
            reg._http_sessions.clear()

            with pytest.raises(MCPServerUnavailable):
                await reg.call_tool("mcp_docker_echo", {"message": "hi"})

            await reg.stop()

    async def test_concurrent_http_calls(self):
        from tests.helpers.fake_mcp_server_http import start_fake_http_server

        async with start_fake_http_server() as url:
            cfg = MCPServerConfig(name="docker", transport="http", url=url)
            reg = MCPRegistry([cfg])
            await reg.start()

            results = await asyncio.gather(
                reg.call_tool("mcp_docker_echo", {"message": "a"}),
                reg.call_tool("mcp_docker_echo", {"message": "b"}),
                reg.call_tool("mcp_docker_echo", {"message": "c"}),
            )
            texts = {r[0]["text"] for r in results}
            assert texts == {"a", "b", "c"}

            await reg.stop()


# ---------------------------------------------------------------------------
# 9. Mixed stdio + http
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestMixedTransports:
    async def test_stdio_and_http_servers_coexist(self):
        from tests.helpers.fake_mcp_server_http import start_fake_http_server

        async with start_fake_http_server() as url:
            reg = MCPRegistry([
                _fake_server_config(name="local"),
                MCPServerConfig(name="docker", transport="http", url=url),
            ])
            await reg.start()

            tools = reg.get_all_tools()
            assert "mcp_local_echo" in tools
            assert "mcp_docker_echo" in tools

            local_result = await reg.call_tool("mcp_local_echo", {"message": "from stdio"})
            http_result = await reg.call_tool("mcp_docker_echo", {"message": "from http"})

            assert local_result[0]["text"] == "from stdio"
            assert http_result[0]["text"] == "from http"

            await reg.stop()
