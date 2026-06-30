"""Tests for provider modules — verify unified ProviderResult return type."""

import io

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

    def test_gemini_models_catalog(self):
        # Model lineup refresh (CDI-1264): Nano Banana 2 (flash) + Nano Banana
        # Pro; gemini-2.5-flash-image dropped.
        from mcp_bildsprache.providers.gemini import GEMINI_MODELS

        assert "gemini-3.1-flash-image-preview" in GEMINI_MODELS
        assert "gemini-3-pro-image-preview" in GEMINI_MODELS
        assert "gemini-2.5-flash-image" not in GEMINI_MODELS

    @pytest.mark.anyio
    async def test_model_override_prefers_requested_model(self, httpx_mock):
        """Passing model= puts that model first in the attempt order — the
        diagram path uses this to prefer Nano Banana Pro (CDI-1264)."""
        import base64 as b64mod

        from mcp_bildsprache.providers.gemini import generate_gemini

        png_bytes = _fake_png_bytes()
        b64 = b64mod.b64encode(png_bytes).decode()
        httpx_mock.add_response(
            json={
                "candidates": [{
                    "content": {"parts": [{
                        "inlineData": {"data": b64, "mimeType": "image/png"}
                    }]}
                }]
            },
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GEMINI_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.gemini.settings", test_settings)

            result = await generate_gemini(
                "test prompt", 1536, 1024, model="gemini-3-pro-image-preview"
            )

        # First (and only) request hits Nano Banana Pro.
        request = httpx_mock.get_request()
        assert "gemini-3-pro-image-preview" in str(request.url)
        assert result.model == "gemini-3-pro-image-preview"


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


class TestGeminiSizeConfig:
    """CDI-1163: constrain render size so calls fit the MCP portal budget."""

    def test_closest_aspect_ratio_landscape(self):
        from mcp_bildsprache.providers.gemini import _closest_aspect_ratio

        # 1536x1024 == 3:2 exactly.
        assert _closest_aspect_ratio(1536, 1024) == "3:2"
        # 1920x1080 == 16:9.
        assert _closest_aspect_ratio(1920, 1080) == "16:9"
        # Square.
        assert _closest_aspect_ratio(1200, 1200) == "1:1"
        # Portrait.
        assert _closest_aspect_ratio(1024, 1536) == "2:3"

    def test_closest_aspect_ratio_degenerate(self):
        from mcp_bildsprache.providers.gemini import _closest_aspect_ratio

        assert _closest_aspect_ratio(0, 100) == "1:1"
        assert _closest_aspect_ratio(100, 0) == "1:1"

    def test_image_size_tier(self):
        from mcp_bildsprache.providers.gemini import _image_size_for

        assert _image_size_for(1024, 1024) == "1K"
        assert _image_size_for(1264, 800) == "1K"
        assert _image_size_for(1536, 1024) == "2K"
        assert _image_size_for(2048, 2048) == "2K"
        # Flash model caps at 2K even for very large targets (portal budget).
        assert _image_size_for(4096, 4096, "gemini-3.1-flash-image-preview") == "2K"
        # Nano Banana Pro is the 4K brand-graphics model (CDI-1264): targets
        # beyond ~2K are allowed to render at 4K.
        assert _image_size_for(4096, 4096, "gemini-3-pro-image-preview") == "4K"
        assert _image_size_for(2048, 2048, "gemini-3-pro-image-preview") == "2K"

    @pytest.mark.anyio
    async def test_payload_sets_image_config_for_gemini3(self, httpx_mock):
        """The 3.x model gets both aspectRatio and imageSize; no 4K default."""
        import base64 as b64mod
        import json as _json

        from mcp_bildsprache.providers.gemini import generate_gemini

        png_bytes = _fake_png_bytes()
        b64 = b64mod.b64encode(png_bytes).decode()
        httpx_mock.add_response(
            json={
                "candidates": [{
                    "content": {"parts": [{
                        "inlineData": {"data": b64, "mimeType": "image/png"}
                    }]}
                }]
            },
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GEMINI_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.gemini.settings", test_settings)

            await generate_gemini("test prompt", 1536, 1024)

        # First (and only successful) request goes to the 3.x model.
        request = httpx_mock.get_request()
        assert "gemini-3" in str(request.url)
        # Auth is via header, never the URL query string (no key leak in errors).
        assert "key=" not in str(request.url)
        assert request.headers.get("x-goog-api-key") == "test-key"
        body = _json.loads(request.content)
        image_cfg = body["generationConfig"]["imageConfig"]
        assert image_cfg["aspectRatio"] == "3:2"
        assert image_cfg["imageSize"] == "2K"
        # The redundant "Target dimensions" hint is gone now that the aspect
        # ratio is set structurally.
        assert "Target dimensions" not in body["contents"][0]["parts"][0]["text"]

    @pytest.mark.anyio
    async def test_flash_failure_falls_to_nano_banana_pro(self, httpx_mock):
        """gemini-2.5-flash-image was dropped (CDI-1264). When the flash model
        fails, the fallback is Nano Banana Pro (gemini-3-pro-image-preview),
        which still sets imageSize (it is a 3.x model)."""
        import base64 as b64mod
        import json as _json

        from mcp_bildsprache.providers.gemini import generate_gemini

        png_bytes = _fake_png_bytes()
        b64 = b64mod.b64encode(png_bytes).decode()
        # First model (3.1 flash) fails, second model (3-pro) succeeds.
        httpx_mock.add_response(status_code=503)
        httpx_mock.add_response(
            json={
                "candidates": [{
                    "content": {"parts": [{
                        "inlineData": {"data": b64, "mimeType": "image/png"}
                    }]}
                }]
            },
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GEMINI_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.gemini.settings", test_settings)

            result = await generate_gemini("test prompt", 1536, 1024)

        assert result.model == "gemini-3-pro-image-preview"
        # The second request is the Nano Banana Pro call.
        last = httpx_mock.get_requests()[-1]
        assert "gemini-3-pro-image-preview" in str(last.url)
        image_cfg = _json.loads(last.content)["generationConfig"]["imageConfig"]
        assert "aspectRatio" in image_cfg
        # 3.x models set imageSize; 1536px long edge → 2K tier.
        assert image_cfg["imageSize"] == "2K"


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
