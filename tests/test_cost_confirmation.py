"""Tests for the defensive cost-confirmation elicitation (shelf item).

generate_image and generate_diagram elicit a yes/no cost confirmation before
the paid provider call. Per the defensive-elicit contract:

- Clients without elicitation support (ctx.elicit raises) must NEVER break —
  generation proceeds with the existing behaviour.
- ctx is None (direct/unit calls) proceeds.
- An explicit decline/cancel (from a client that DOES support elicitation)
  aborts cleanly with cancelled=True and writes no artifact.
- An explicit accept (yes) proceeds; an explicit "no" (data=False) aborts.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from mcp_bildsprache.types import ProviderResult


def _fake_provider_result(model: str = "gpt-image-2") -> ProviderResult:
    buf = io.BytesIO()
    Image.new("RGB", (1024, 1024), color=(80, 120, 160)).save(buf, format="PNG")
    return ProviderResult(
        image_data=buf.getvalue(),
        mime_type="image/png",
        model=model,
        cost_estimate="$0.05",
    )


@pytest.fixture
def mock_provider():
    result = _fake_provider_result()
    mock = AsyncMock(return_value=result)
    with patch(
        "mcp_bildsprache.server.PROVIDERS",
        {"openai": mock, "gemini": mock, "flux": mock, "recraft": mock},
    ):
        yield mock


class _FakeElicitResult:
    def __init__(self, action: str, data=None) -> None:
        self.action = action
        self.data = data


class _FakeCtx:
    """Minimal Context double exposing the surface the tools touch."""

    def __init__(self, elicit_behavior) -> None:
        # elicit_behavior: a callable(message, response_type) -> result, or an
        # Exception instance to raise (simulating no elicitation support).
        self._elicit_behavior = elicit_behavior
        self.elicit_called = False

    async def report_progress(self, **kwargs):
        return None

    async def info(self, message):
        return None

    async def elicit(self, message, response_type=None):
        self.elicit_called = True
        if isinstance(self._elicit_behavior, Exception):
            raise self._elicit_behavior
        return self._elicit_behavior(message, response_type)


def _accept(data=True):
    return lambda msg, rt: _FakeElicitResult("accept", data)


def _decline():
    return lambda msg, rt: _FakeElicitResult("decline")


def _cancel():
    return lambda msg, rt: _FakeElicitResult("cancel")


# ---------------------------------------------------------------------------
# generate_image
# ---------------------------------------------------------------------------


class TestGenerateImageCostConfirmation:
    @pytest.mark.anyio
    async def test_no_ctx_proceeds_unchanged(self, tmp_path: Path, mock_provider):
        """ctx=None (direct call) must generate exactly as before."""
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(prompt="x", dimensions="512x512")

        assert result.get("cancelled") is not True
        assert "hosted_url" in result
        mock_provider.assert_awaited()

    @pytest.mark.anyio
    async def test_unsupported_elicit_proceeds(self, tmp_path: Path, mock_provider):
        """Client without elicitation support → elicit raises → proceed."""
        from mcp_bildsprache.server import generate_image

        ctx = _FakeCtx(RuntimeError("elicitation not supported"))
        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(prompt="x", dimensions="512x512", ctx=ctx)

        assert ctx.elicit_called is True
        assert result.get("cancelled") is not True
        assert "hosted_url" in result
        mock_provider.assert_awaited()

    @pytest.mark.anyio
    async def test_accept_yes_proceeds_and_surfaces_cost(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        ctx = _FakeCtx(_accept(True))
        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(prompt="x", dimensions="512x512", ctx=ctx)

        assert "hosted_url" in result
        assert result.get("cancelled") is not True
        mock_provider.assert_awaited()

    @pytest.mark.anyio
    async def test_decline_aborts_without_provider_call(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        ctx = _FakeCtx(_decline())
        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(prompt="x", dimensions="512x512", ctx=ctx)

        assert result["cancelled"] is True
        assert "hosted_url" not in result
        assert "estimated_cost_eur" in result
        mock_provider.assert_not_awaited()
        # No artifact written.
        assert list(tmp_path.rglob("*.webp")) == []

    @pytest.mark.anyio
    async def test_cancel_aborts_without_provider_call(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        ctx = _FakeCtx(_cancel())
        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(prompt="x", dimensions="512x512", ctx=ctx)

        assert result["cancelled"] is True
        mock_provider.assert_not_awaited()

    @pytest.mark.anyio
    async def test_accept_no_aborts(self, tmp_path: Path, mock_provider):
        """Accept action but a literal 'no' (data=False) is an explicit decline."""
        from mcp_bildsprache.server import generate_image

        ctx = _FakeCtx(_accept(False))
        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(prompt="x", dimensions="512x512", ctx=ctx)

        assert result["cancelled"] is True
        mock_provider.assert_not_awaited()


# ---------------------------------------------------------------------------
# generate_diagram
# ---------------------------------------------------------------------------


class TestGenerateDiagramCostConfirmation:
    @pytest.mark.anyio
    async def test_unsupported_elicit_proceeds(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_diagram

        ctx = _FakeCtx(RuntimeError("elicitation not supported"))
        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_diagram(format="flow", prompt="a -> b", ctx=ctx)

        assert ctx.elicit_called is True
        assert result.get("cancelled") is not True
        assert "hosted_url" in result
        mock_provider.assert_awaited()

    @pytest.mark.anyio
    async def test_decline_aborts_without_provider_call(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_diagram

        ctx = _FakeCtx(_decline())
        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_diagram(format="flow", prompt="a -> b", ctx=ctx)

        assert result["cancelled"] is True
        assert "hosted_url" not in result
        assert result["format"] == "flow"
        assert result["brand_context"] == "casey"
        mock_provider.assert_not_awaited()
        assert list(tmp_path.rglob("*.webp")) == []

    @pytest.mark.anyio
    async def test_cancel_aborts_without_provider_call(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_diagram

        ctx = _FakeCtx(_cancel())
        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_diagram(format="flow", prompt="a -> b", ctx=ctx)

        assert result["cancelled"] is True
        mock_provider.assert_not_awaited()

    @pytest.mark.anyio
    async def test_accept_yes_proceeds(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_diagram

        ctx = _FakeCtx(_accept(True))
        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_diagram(format="flow", prompt="a -> b", ctx=ctx)

        assert "hosted_url" in result
        assert result.get("cancelled") is not True
        mock_provider.assert_awaited()

    @pytest.mark.anyio
    async def test_error_paths_skip_elicit(self):
        """Validation errors return before the cost gate — no elicit, no cost."""
        from mcp_bildsprache.server import generate_diagram

        ctx = _FakeCtx(_decline())
        result = await generate_diagram(format="flow", ctx=ctx)  # no prompt/mermaid

        assert result["error"]["code"] == "INVALID_INPUT"
        assert ctx.elicit_called is False
