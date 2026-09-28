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
    assert ann.read_only_hint is True
    assert ann.open_world_hint is False
    assert tools["generate_image"].annotations.read_only_hint is False


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
    props = tools["generate_image"].input_schema["properties"]
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


@pytest.mark.asyncio
async def test_models_serialise_by_alias_over_the_wire():
    # Tools return models; `register_` must still reach clients as `register`.
    async with Client(mcp) as client:
        result = await client.call_tool(
            "generate_prompt", {"prompt": "x", "context": "casey", "register": "personal"}
        )
    assert result.structured_content["register"] == "personal"
    assert "register_" not in result.structured_content


@pytest.mark.asyncio
async def test_models_resource_reads():
    async with Client(mcp) as client:
        contents = await client.read_resource("bildsprache://models")
    assert '"diagram_formats"' in contents[0].text


@pytest.mark.asyncio
async def test_gpt_image_25_models_and_quality_on_the_surface():
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for name in ("generate_image", "generate_prompt"):
        enum = tools[name].input_schema["properties"]["model"]["anyOf"][0]["enum"]
        assert {"gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"} <= set(enum)
    props = tools["generate_image"].input_schema["properties"]
    assert set(props["quality"]["anyOf"][0]["enum"]) == {"low", "medium", "high", "xhigh", "max", "auto"}
    assert "transparent" in props


@pytest.mark.asyncio
async def test_disabled_provider_is_a_tool_error(capsys):
    # generate_prompt never calls a provider, so this stays offline.
    from fastmcp.exceptions import ToolError

    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="PROVIDER_TEMPORARILY_DISABLED"):
            await client.call_tool("generate_prompt", {"prompt": "x", "model": "flux"})
    lines = [ln for ln in capsys.readouterr().err.splitlines() if '"mcp_usage"' in ln]
    assert '"outcome": "error"' in lines[-1]


@pytest.mark.asyncio
async def test_validation_failures_are_tool_errors():
    from fastmcp.exceptions import ToolError

    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="INVALID_INPUT"):
            await client.call_tool("generate_diagram", {"format": "flow"})
        with pytest.raises(ToolError, match="INVALID_SINCE"):
            await client.call_tool("generation_stats", {"since": "not-a-date"})
