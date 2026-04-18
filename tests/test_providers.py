"""Tests for provider modules — verify unified ProviderResult return type."""

import io
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from mcp_bildsprache.types import ProviderResult


def _fake_png_bytes(width: int = 512, height: int = 512) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(50, 100, 150)).save(buf, format="PNG")
    return buf.getvalue()


def _fake_jpeg_bytes(width: int = 512, height: int = 512) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(50, 100, 150)).save(buf, format="JPEG")
    return buf.getvalue()


def _fake_webp_bytes(width: int = 256, height: int = 256) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(50, 100, 150)).save(buf, format="WEBP")
    return buf.getvalue()


class TestBflProvider:
    @pytest.mark.anyio
    async def test_returns_provider_result(self, httpx_mock):
        from mcp_bildsprache.providers.bfl import generate_bfl

        # Mock API key
        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/flux-2-max",
            json={"id": "task-123"},
        )
        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/get_result?id=task-123",
            json={"status": "Ready", "result": {"sample": "https://example.com/img.jpg"}},
        )
        httpx_mock.add_response(
            url="https://example.com/img.jpg",
            content=_fake_jpeg_bytes(),
            headers={"content-type": "image/jpeg"},
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("BFL_API_KEY", "test-key")
            # Reload settings to pick up env
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.bfl.settings", test_settings)

            result = await generate_bfl("test prompt", 512, 512)

        assert isinstance(result, ProviderResult)
        assert len(result.image_data) > 0
        assert result.mime_type == "image/jpeg"
        assert "flux" in result.model


class TestGeminiProvider:
    @pytest.mark.anyio
    async def test_returns_provider_result(self, httpx_mock):
        import base64

        from mcp_bildsprache.providers.gemini import generate_gemini

        png_bytes = _fake_png_bytes()
        b64 = base64.b64encode(png_bytes).decode()

        httpx_mock.add_response(
            json={
                "candidates": [{
                    "content": {
                        "parts": [{
                            "inlineData": {
                                "data": b64,
                                "mimeType": "image/png",
                            }
                        }]
                    }
                }]
            },
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GEMINI_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.gemini.settings", test_settings)

            result = await generate_gemini("test prompt", 512, 512)

        assert isinstance(result, ProviderResult)
        assert result.image_data == png_bytes
        assert result.mime_type == "image/png"
        assert "gemini" in result.model


class TestGeminiReferences:
    @pytest.mark.anyio
    async def test_references_become_inline_data_parts_in_order(self, httpx_mock):
        import base64 as b64mod

        from mcp_bildsprache.providers.gemini import generate_gemini

        png_bytes = _fake_png_bytes()
        b64 = b64mod.b64encode(png_bytes).decode()

        httpx_mock.add_response(
            json={
                "candidates": [{
                    "content": {
                        "parts": [{
                            "inlineData": {"data": b64, "mimeType": "image/png"}
                        }]
                    }
                }]
            },
        )

        ref_a = _fake_png_bytes(64, 64)
        ref_b = _fake_webp_bytes(64, 64)

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GEMINI_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.gemini.settings", test_settings)

            result = await generate_gemini(
                "test prompt", 512, 512, reference_images=[ref_a, ref_b]
            )

        assert isinstance(result, ProviderResult)

        # Inspect the outbound request payload.
        request = httpx_mock.get_request()
        import json as _json
        body = _json.loads(request.content)
        parts = body["contents"][0]["parts"]
        assert len(parts) == 3
        assert "text" in parts[0]
        assert parts[1]["inlineData"]["mimeType"] == "image/png"
        assert parts[1]["inlineData"]["data"] == b64mod.b64encode(ref_a).decode("ascii")
        assert parts[2]["inlineData"]["mimeType"] == "image/webp"
        assert parts[2]["inlineData"]["data"] == b64mod.b64encode(ref_b).decode("ascii")

    @pytest.mark.anyio
    async def test_unsupported_mime_raises_valueerror_pre_request(self, httpx_mock):
        from mcp_bildsprache.providers.gemini import generate_gemini

        bogus_bytes = b"this is not an image"

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GEMINI_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.gemini.settings", test_settings)

            with pytest.raises(ValueError, match=r"reference_images\[0\]"):
                await generate_gemini(
                    "test prompt", 512, 512, reference_images=[bogus_bytes]
                )

        # No HTTP request should have been made.
        assert len(httpx_mock.get_requests()) == 0

    @pytest.mark.anyio
    async def test_none_behaves_unchanged(self, httpx_mock):
        """Passing reference_images=None should match the text-only code path byte-for-byte."""
        import base64 as b64mod
        import json as _json

        from mcp_bildsprache.providers.gemini import generate_gemini

        png_bytes = _fake_png_bytes()
        b64 = b64mod.b64encode(png_bytes).decode()

        httpx_mock.add_response(
            json={
                "candidates": [{
                    "content": {
                        "parts": [{
                            "inlineData": {"data": b64, "mimeType": "image/png"}
                        }]
                    }
                }]
            },
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GEMINI_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.gemini.settings", test_settings)

            await generate_gemini("test prompt", 512, 512)

        body = _json.loads(httpx_mock.get_request().content)
        parts = body["contents"][0]["parts"]
        assert len(parts) == 1
        assert "text" in parts[0]


class TestRecraftProvider:
    @pytest.mark.anyio
    async def test_returns_provider_result(self, httpx_mock):
        from mcp_bildsprache.providers.recraft import generate_recraft

        png_bytes = _fake_png_bytes()

        httpx_mock.add_response(
            url="https://external.api.recraft.ai/v1/images/generations",
            json={"data": [{"url": "https://example.com/recraft.png"}]},
        )
        httpx_mock.add_response(
            url="https://example.com/recraft.png",
            content=png_bytes,
            headers={"content-type": "image/png"},
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("RECRAFT_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.recraft.settings", test_settings)

            result = await generate_recraft("test prompt", 1024, 1024)

        assert isinstance(result, ProviderResult)
        assert result.image_data == png_bytes
        assert result.mime_type == "image/png"
        assert "recraft" in result.model


class TestBflProviderErrors:
    @pytest.mark.anyio
    async def test_no_api_key_raises(self):
        from mcp_bildsprache.providers.bfl import generate_bfl

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("BFL_API_KEY", "")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.bfl.settings", test_settings)

            with pytest.raises(ValueError, match="BFL_API_KEY not configured"):
                await generate_bfl("test prompt", 512, 512)

    @pytest.mark.anyio
    async def test_polling_timeout(self):
        """Verify that _generate_with_model raises TimeoutError after polling loop exhaustion."""
        from mcp_bildsprache.providers.bfl import _generate_with_model, FLUX_MODELS
        import asyncio
        import httpx
        from unittest.mock import patch as std_patch

        model_info = FLUX_MODELS["flux-2-max"]

        # Build fake httpx responses
        async def fake_post(url, **kwargs):
            resp = httpx.Response(200, json={"id": "task-timeout"})
            resp._request = httpx.Request("POST", url)
            return resp

        async def fake_get(url, **kwargs):
            resp = httpx.Response(200, json={"status": "Pending"})
            resp._request = httpx.Request("GET", url)
            return resp

        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with std_patch("mcp_bildsprache.providers.bfl.httpx.AsyncClient", return_value=mock_client), \
             std_patch.object(asyncio, "sleep", new=AsyncMock()):
            with pytest.raises(TimeoutError, match="timed out"):
                await _generate_with_model(
                    "test-key", "flux-2-max", model_info, "test prompt", 512, 512
                )

    @pytest.mark.anyio
    async def test_all_models_fail(self, httpx_mock):
        from mcp_bildsprache.providers.bfl import generate_bfl

        # All model submission endpoints return errors
        for model in ["flux-2-max", "flux-2-pro", "flux-pro-1.1"]:
            httpx_mock.add_response(
                url=f"https://api.bfl.ai/v1/{model}",
                status_code=500,
            )

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("BFL_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.bfl.settings", test_settings)

            with pytest.raises(Exception):
                await generate_bfl("test prompt", 512, 512)


class TestGeminiProviderErrors:
    @pytest.mark.anyio
    async def test_no_api_key_raises(self):
        from mcp_bildsprache.providers.gemini import generate_gemini

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GEMINI_API_KEY", "")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.gemini.settings", test_settings)

            with pytest.raises(ValueError, match="GEMINI_API_KEY not configured"):
                await generate_gemini("test prompt", 512, 512)

    @pytest.mark.anyio
    async def test_no_candidates_raises(self, httpx_mock):
        from mcp_bildsprache.providers.gemini import generate_gemini

        # Both model attempts return no candidates
        httpx_mock.add_response(json={"candidates": []})
        httpx_mock.add_response(json={"candidates": []})

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GEMINI_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.gemini.settings", test_settings)

            with pytest.raises(ValueError, match="no candidates"):
                await generate_gemini("test prompt", 512, 512)

    @pytest.mark.anyio
    async def test_no_image_in_parts_raises(self, httpx_mock):
        from mcp_bildsprache.providers.gemini import generate_gemini

        text_only_response = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "Here is an image description"}]
                }
            }]
        }
        # Both model attempts return text-only parts
        httpx_mock.add_response(json=text_only_response)
        httpx_mock.add_response(json=text_only_response)

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GEMINI_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.gemini.settings", test_settings)

            with pytest.raises(ValueError, match="no image data"):
                await generate_gemini("test prompt", 512, 512)


class TestRecraftProviderErrors:
    @pytest.mark.anyio
    async def test_no_api_key_raises(self):
        from mcp_bildsprache.providers.recraft import generate_recraft

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("RECRAFT_API_KEY", "")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.recraft.settings", test_settings)

            with pytest.raises(ValueError, match="RECRAFT_API_KEY not configured"):
                await generate_recraft("test prompt", 1024, 1024)

    @pytest.mark.anyio
    async def test_no_images_raises(self, httpx_mock):
        from mcp_bildsprache.providers.recraft import generate_recraft

        httpx_mock.add_response(
            url="https://external.api.recraft.ai/v1/images/generations",
            json={"data": []},
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("RECRAFT_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.recraft.settings", test_settings)

            with pytest.raises(ValueError, match="no images"):
                await generate_recraft("test prompt", 1024, 1024)
