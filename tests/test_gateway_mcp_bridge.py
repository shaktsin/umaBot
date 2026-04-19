from __future__ import annotations

import base64

import pytest

from umabot.gateway import _mcp_content_to_tool_result, _register_mcp_tools
from umabot.tools import ToolRegistry, UnifiedToolRegistry


def test_mcp_content_to_tool_result_includes_image_attachment() -> None:
    raw = b"fake-png-bytes"
    content = [
        {"type": "text", "text": "Screenshot captured."},
        {
            "type": "image",
            "data": base64.b64encode(raw).decode(),
            "mimeType": "image/png",
            "filename": "landing.png",
        },
    ]

    result = _mcp_content_to_tool_result("mcp_playwright_screenshot", content)

    assert result.content == "Screenshot captured."
    assert len(result.attachments) == 1
    assert result.attachments[0].filename == "landing.png"
    assert result.attachments[0].mime_type == "image/png"
    assert result.attachments[0].data == raw


def test_mcp_content_to_tool_result_generates_fallback_text_for_attachments() -> None:
    raw = b"img"
    content = [{"type": "image", "data": base64.b64encode(raw).decode(), "mimeType": "image/png"}]

    result = _mcp_content_to_tool_result("mcp_playwright_screenshot", content)

    assert result.content == "Generated 1 attachment(s)."
    assert len(result.attachments) == 1


class _FakeMCPRegistry:
    def get_all_tools(self):
        return {
            "mcp_playwright_screenshot": {
                "description": "Capture screenshot",
                "schema": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                    "additionalProperties": False,
                },
            }
        }

    async def call_tool(self, prefixed_name: str, arguments: dict):
        assert prefixed_name == "mcp_playwright_screenshot"
        assert arguments["url"] == "http://127.0.0.1:4173"
        return [
            {"type": "text", "text": "ok"},
            {
                "type": "image",
                "data": base64.b64encode(b"bytes").decode(),
                "mimeType": "image/png",
                "filename": "landing.png",
            },
        ]


@pytest.mark.asyncio
async def test_register_mcp_tools_bridges_into_tool_registry() -> None:
    tool_registry = ToolRegistry()
    unified = UnifiedToolRegistry()
    fake_mcp = _FakeMCPRegistry()

    _register_mcp_tools(tool_registry, unified, fake_mcp)  # type: ignore[arg-type]

    tool = tool_registry.get("mcp_playwright_screenshot")
    assert tool is not None

    result = await tool.handler({"url": "http://127.0.0.1:4173"})
    assert result.content == "ok"
    assert len(result.attachments) == 1
    assert result.attachments[0].filename == "landing.png"
