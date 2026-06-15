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


class TestBflReferences:
    # flux-kontext-pro was dropped (model lineup refresh, CDI-1264). The
    # reference-bearing FLUX path is now flux-2-pro (image_prompt) only.
    @pytest.mark.anyio
    async def test_single_ref_routes_to_flux_2_pro(self, httpx_mock):
        import base64 as b64mod
        import json as _json

        from mcp_bildsprache.providers.bfl import generate_bfl

        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/flux-2-pro",
            json={"id": "task-ref-1"},
        )
        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/get_result?id=task-ref-1",
            json={"status": "Ready", "result": {"sample": "https://example.com/out.jpg"}},
        )
        httpx_mock.add_response(
            url="https://example.com/out.jpg",
            content=_fake_jpeg_bytes(),
            headers={"content-type": "image/jpeg"},
        )

        ref_png = _fake_png_bytes(128, 128)

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("BFL_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.bfl.settings", test_settings)

            result = await generate_bfl(
                "ref-bearing", 512, 512, reference_images=[ref_png]
            )

        # First outbound request must hit flux-2-pro with image_prompt set.
        submit = httpx_mock.get_requests()[0]
        assert submit.url.path.endswith("/flux-2-pro")
        body = _json.loads(submit.content)
        assert "image_prompt" in body
        assert isinstance(body["image_prompt"], str)
        # round-trips through base64
        assert b64mod.b64decode(body["image_prompt"])

        assert result.model == "flux-2-pro"
        assert result.cost_estimate == "$0.03"

    @pytest.mark.anyio
    async def test_multi_ref_is_collaged_and_submitted(self, httpx_mock, caplog):
        import base64 as b64mod
        import io as _io
        import json as _json
        import logging

        from mcp_bildsprache.providers.bfl import generate_bfl

        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/flux-2-pro",
            json={"id": "task-collage"},
        )
        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/get_result?id=task-collage",
            json={"status": "Ready", "result": {"sample": "https://example.com/out.jpg"}},
        )
        httpx_mock.add_response(
            url="https://example.com/out.jpg",
            content=_fake_jpeg_bytes(),
            headers={"content-type": "image/jpeg"},
        )

        refs = [_fake_png_bytes(128, 128), _fake_webp_bytes(128, 128), _fake_png_bytes(128, 128)]

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("BFL_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.bfl.settings", test_settings)

            with caplog.at_level(logging.INFO, logger="mcp_bildsprache.providers.bfl"):
                await generate_bfl("multi", 512, 512, reference_images=refs)

        # Collage INFO log was emitted with sources=3.
        assert any("bfl_collage" in r.message and "sources=3" in r.message
                   for r in caplog.records)

        # image_prompt on the submitted payload is a valid PNG (our collage output).
        submit = httpx_mock.get_requests()[0]
        body = _json.loads(submit.content)
        raw = b64mod.b64decode(body["image_prompt"])
        img = Image.open(_io.BytesIO(raw))
        assert img.format == "PNG"
        # 3 × 1024-high sources would exceed 1 MP so the collage is downscaled.
        # Just assert it is within the flux-2-pro reference budget.
        mp_out = (img.width * img.height) / 1_000_000
        assert mp_out <= 1.01

    @pytest.mark.anyio
    async def test_flux_2_max_never_called_with_refs(self, httpx_mock):
        from mcp_bildsprache.providers.bfl import generate_bfl

        # The reference-capable endpoint fails — we expect the function to
        # raise WITHOUT ever trying flux-2-max (text-only) or flux-pro-1.1.
        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/flux-2-pro",
            status_code=500,
        )

        ref = _fake_png_bytes(64, 64)

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("BFL_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.bfl.settings", test_settings)

            with pytest.raises(Exception):
                await generate_bfl("fail", 512, 512, reference_images=[ref])

        urls = [str(r.url) for r in httpx_mock.get_requests()]
        assert not any("/flux-2-max" in u for u in urls)
        assert not any("/flux-pro-1.1" in u for u in urls)
        assert not any("/flux-kontext-pro" in u for u in urls)


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


class TestRecraftProvider:
    @pytest.mark.anyio
    async def test_returns_provider_result(self, httpx_mock):
        import json as _json

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
        # Recraft V4.1 upgrade (model lineup refresh, CDI-1264): display id and
        # the outbound API slug both move to the v4.1 model.
        assert result.model == "recraft-v4.1"
        submit = httpx_mock.get_requests()[0]
        assert _json.loads(submit.content)["model"] == "recraftv4_1"


class TestRecraftReferences:
    @pytest.mark.anyio
    async def test_refs_are_dropped_with_info_log_and_text_only_request(
        self, httpx_mock, caplog
    ):
        import json as _json
        import logging

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

            with caplog.at_level(logging.INFO, logger="mcp_bildsprache.providers.recraft"):
                result = await generate_recraft(
                    "test", 1024, 1024, reference_images=[png_bytes, png_bytes]
                )

        # Exactly one INFO log naming the drop + count.
        drop_records = [r for r in caplog.records
                        if "dropped 2 reference image(s)" in r.message]
        assert len(drop_records) == 1

        # The outbound request must be text-only (no input_image / image_prompt).
        submit = httpx_mock.get_requests()[0]
        body = _json.loads(submit.content)
        assert "input_image" not in body
        assert "image_prompt" not in body
        assert "reference_images" not in body

        assert result.model == "recraft-v4.1"


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
