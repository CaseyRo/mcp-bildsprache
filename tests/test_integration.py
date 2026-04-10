"""Integration tests for generate_image end-to-end with hosting pipeline."""

import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from mcp_bildsprache.storage import StorageError
from mcp_bildsprache.types import ProviderResult


def _fake_provider_result() -> ProviderResult:
    buf = io.BytesIO()
    Image.new("RGB", (1024, 1024), color=(80, 120, 160)).save(buf, format="PNG")
    return ProviderResult(
        image_data=buf.getvalue(),
        mime_type="image/png",
        model="flux-2-max",
        cost_estimate="$0.07",
    )


@pytest.fixture
def mock_provider():
    """Mock all providers to return a fake ProviderResult."""
    result = _fake_provider_result()
    mock = AsyncMock(return_value=result)
    with patch("mcp_bildsprache.server.PROVIDERS", {"flux": mock, "gemini": mock, "recraft": mock}):
        yield mock


class TestGenerateImageHosting:
    @pytest.mark.anyio
    async def test_hosting_enabled_returns_hosted_url(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="a beautiful sunset",
                context="@casey.berlin",
                platform="blog-hero",
            )

        assert "hosted_url" in result
        assert result["hosted_url"].startswith("https://img.cdit-works.de/casey-berlin/")
        assert result["hosted_url"].endswith(".webp")
        assert result["dimensions"] == "1600x900"
        assert result["model"] == "flux-2-max"
        assert "image_base64" not in result  # No raw data by default

        # Verify file was written
        webp_files = list(tmp_path.rglob("*.webp"))
        assert len(webp_files) == 1

        # Verify image dimensions are exact
        img = Image.open(webp_files[0])
        assert img.size == (1600, 900)

        # Verify sidecar
        json_files = list(tmp_path.rglob("*.json"))
        assert len(json_files) == 1
        sidecar = json.loads(json_files[0].read_text())
        assert sidecar["brand_context"] == "@casey.berlin"

    @pytest.mark.anyio
    async def test_always_returns_hosted_url(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(prompt="test", dimensions="512x512")

        assert "hosted_url" in result
        assert result["response_mode"] == "url"
        assert "image_base64" not in result


class TestRawMode:
    @pytest.mark.anyio
    async def test_raw_returns_url_not_base64(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="test raw mode",
                dimensions="800x600",
                raw=True,
            )

        assert "hosted_url" in result
        assert "raw_url" in result
        assert result["raw_url"].startswith("https://img.cdit-works.de/")
        assert result["raw_url"].endswith("-raw.png")  # Original format, not WebP
        assert result["raw_mime_type"] == "image/png"
        assert "raw_image_base64" not in result  # No base64

        # Verify raw file was actually written
        raw_files = list(tmp_path.rglob("*-raw.png"))
        assert len(raw_files) == 1

    @pytest.mark.anyio
    async def test_raw_false_excludes_raw(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="test no raw",
                dimensions="800x600",
                raw=False,
            )

        assert "hosted_url" in result
        assert "raw_url" not in result


class TestProviderFallback:
    @pytest.mark.anyio
    async def test_primary_fails_fallback_succeeds(self, tmp_path: Path):
        """When primary provider fails, fallback kicks in with intended_provider and fallback_reason."""
        result_data = _fake_provider_result()
        failing_mock = AsyncMock(side_effect=RuntimeError("Provider down"))
        success_mock = AsyncMock(return_value=result_data)

        providers = {"flux": failing_mock, "gemini": success_mock, "recraft": success_mock}

        with patch("mcp_bildsprache.server.PROVIDERS", providers), \
             patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            from mcp_bildsprache.server import generate_image
            result = await generate_image(prompt="test fallback", dimensions="512x512")

        assert result["fallback_used"] is True
        assert result["intended_provider"] == "flux"
        assert result["fallback_reason"] == "provider_error"
        assert "hosted_url" in result

    @pytest.mark.anyio
    async def test_storage_error_propagates(self, tmp_path: Path, mock_provider):
        """StorageError should propagate — no base64 fallback."""
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.server.store_image", side_effect=StorageError("disk full")):
            with pytest.raises(StorageError, match="disk full"):
                await generate_image(prompt="test storage fail", dimensions="512x512")


class TestDimensionHandling:
    @pytest.mark.anyio
    async def test_explicit_dimensions_override_platform(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="test dimensions",
                platform="blog-hero",      # Would be 1600x900
                dimensions="800x600",       # Should override
            )

        assert result["dimensions"] == "800x600"
        webp_files = list(tmp_path.rglob("*.webp"))
        assert len(webp_files) == 1
        img = Image.open(webp_files[0])
        assert img.size == (800, 600)

    @pytest.mark.anyio
    async def test_default_dimensions_1200x1200(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(prompt="test default size")

        assert result["dimensions"] == "1200x1200"


class TestOtherTools:
    @pytest.mark.anyio
    async def test_generate_prompt_basic(self):
        from mcp_bildsprache.server import generate_prompt

        result = await generate_prompt(
            prompt="a sunset over Berlin",
            context="@casey.berlin",
            platform="blog-hero",
        )

        assert "engineered_prompt" in result
        assert "a sunset over Berlin" in result["engineered_prompt"]
        assert result["model"] == "flux"
        assert result["dimensions"] == "1600x900"
        assert result["brand_context"] == "@casey.berlin"

    @pytest.mark.anyio
    async def test_list_models_returns_entries_when_keys_set(self):
        from mcp_bildsprache.server import list_models

        with patch("mcp_bildsprache.server.settings") as s:
            from pydantic import SecretStr
            s.gemini_api_key = SecretStr("fake-key")
            s.bfl_api_key = SecretStr("fake-key")
            s.recraft_api_key = SecretStr("fake-key")

            result = await list_models()

        assert len(result) == 3
        ids = {m["id"] for m in result}
        assert ids == {"gemini", "flux", "recraft"}

    @pytest.mark.anyio
    async def test_get_visual_presets_returns_presets(self):
        from mcp_bildsprache.server import get_visual_presets

        result = await get_visual_presets()
        assert "presets" in result
        assert "platforms" in result
        assert "casey.berlin" in result["presets"]

    @pytest.mark.anyio
    async def test_get_visual_presets_specific_context(self):
        from mcp_bildsprache.server import get_visual_presets

        result = await get_visual_presets(context="@casey.berlin")
        assert "context" in result
        assert "preset" in result
        assert "European editorial" in result["preset"]
