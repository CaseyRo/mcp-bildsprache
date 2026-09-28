"""The MCP surface itself: server stands up, registers its tools, dispatches a call.

Every other test calls tool functions directly, so nothing else would notice a
framework upgrade that stops the server importing, drops a tool from the
manifest, or breaks dispatch. Pattern: mcp-stolperfalle tests/test_mcp_protocol.py.

Never call a generation tool here: they hit paid APIs.
"""

from __future__ import annotations

import pytest
from fastmcp import Client

from mcp_bildsprache.server import mcp

EXPECTED_TOOLS = {
    "generate_image", "generate_diagram", "generate_prompt", "list_models",
    "list_recent_generations", "generation_stats", "get_image_result",
    "get_visual_presets",
}


def _hint(annotations, snake: str, camel: str):
    # fastmcp 3 exposes camelCase, 4 snake_case; read whichever exists.
    return getattr(annotations, snake, getattr(annotations, camel, None))


@pytest.mark.asyncio
async def test_server_registers_its_tools():
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"


@pytest.mark.asyncio
async def test_read_only_annotations_survive_the_wire():
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    ann = tools["get_visual_presets"].annotations
    assert ann is not None
    assert _hint(ann, "read_only_hint", "readOnlyHint") is True
    assert _hint(ann, "open_world_hint", "openWorldHint") is False
    assert _hint(tools["generate_image"].annotations, "read_only_hint", "readOnlyHint") is False


@pytest.mark.asyncio
async def test_a_tool_call_round_trips():
    # Pure in-process: presets are static data, no provider or network involved.
    async with Client(mcp) as client:
        result = await client.call_tool("get_visual_presets", {"context": "casey"})
    assert result.content, "get_visual_presets returned no content"
    assert '"casey"' in result.content[0].text


@pytest.mark.asyncio
async def test_a_tool_call_writes_one_usage_line(capsys):
    async with Client(mcp) as client:
        await client.call_tool("get_visual_presets", {"context": "casey"})
    lines = [ln for ln in capsys.readouterr().err.splitlines() if '"mcp_usage"' in ln]
    assert len(lines) == 1
    assert all(s in lines[0] for s in ('"bildsprache"', '"get_visual_presets"', '"outcome": "ok"'))


@pytest.mark.asyncio
async def test_generate_image_surface_drops_dead_hints():
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    props = tools["generate_image"].inputSchema["properties"]
    assert "draft" not in props
    enum = props["model"]["anyOf"][0]["enum"]
    assert "gpt-image-2" in enum
    assert not {"gpt-image-1.5", "gpt-image-1-mini"} & set(enum)


@pytest.mark.asyncio
async def test_stale_portal_args_are_ignored_not_rejected():
    # A stale portal catalog may still send draft / a retired model hint. They
    # must get past validation; bad dimensions then stop the call before any
    # provider (patched anyway) is reached.
    from unittest.mock import AsyncMock, patch

    boom = AsyncMock(side_effect=AssertionError("provider must not be called"))
    with patch("mcp_bildsprache.server.PROVIDERS", {"openai": boom, "gemini": boom}):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "generate_image",
                {"prompt": "x", "dimensions": "bad", "draft": True, "model": "gpt-image-1.5"},
                raise_on_error=False,
            )
    assert result.is_error
    assert "Invalid dimensions" in result.content[0].text
    boom.assert_not_called()
