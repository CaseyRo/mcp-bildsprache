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
    @pytest.mark.anyio
    async def test_single_ref_routes_to_kontext_pro(self, httpx_mock):
        import base64 as b64mod
        import json as _json

        from mcp_bildsprache.providers.bfl import generate_bfl

        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/flux-kontext-pro",
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

        # First outbound request must hit flux-kontext-pro with input_image set.
        submit = httpx_mock.get_requests()[0]
        assert submit.url.path.endswith("/flux-kontext-pro")
        body = _json.loads(submit.content)
        assert "input_image" in body
        assert isinstance(body["input_image"], str)
        # round-trips through base64
        assert b64mod.b64decode(body["input_image"])

        assert result.model == "flux-kontext-pro"
        assert result.cost_estimate == "$0.04"

    @pytest.mark.anyio
    async def test_multi_ref_is_collaged_and_submitted(self, httpx_mock, caplog):
        import base64 as b64mod
        import io as _io
        import json as _json
        import logging

        from mcp_bildsprache.providers.bfl import generate_bfl

        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/flux-kontext-pro",
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

        # input_image on the submitted payload is a valid PNG (our collage output).
        submit = httpx_mock.get_requests()[0]
        body = _json.loads(submit.content)
        raw = b64mod.b64decode(body["input_image"])
        img = Image.open(_io.BytesIO(raw))
        assert img.format == "PNG"
        # 3 × 1024-high sources would exceed 1 MP so the collage is downscaled.
        # Just assert it is within the kontext-pro budget.
        mp_out = (img.width * img.height) / 1_000_000
        assert mp_out <= 1.01

    @pytest.mark.anyio
    async def test_kontext_pro_failure_falls_to_flux_2_pro_image_prompt(self, httpx_mock):
        import json as _json

        from mcp_bildsprache.providers.bfl import generate_bfl

        # kontext-pro errors on submit.
        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/flux-kontext-pro",
            status_code=500,
        )
        # flux-2-pro succeeds.
        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/flux-2-pro",
            json={"id": "task-2pro"},
        )
        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/get_result?id=task-2pro",
            json={"status": "Ready", "result": {"sample": "https://example.com/out.jpg"}},
        )
        httpx_mock.add_response(
            url="https://example.com/out.jpg",
            content=_fake_jpeg_bytes(),
            headers={"content-type": "image/jpeg"},
        )

        ref = _fake_png_bytes(64, 64)

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("BFL_API_KEY", "test-key")
            from mcp_bildsprache.config import Settings
            test_settings = Settings()
            mp.setattr("mcp_bildsprache.providers.bfl.settings", test_settings)

            result = await generate_bfl("fallback", 512, 512, reference_images=[ref])

        # Assert flux-2-pro was called with the image_prompt field.
        reqs = httpx_mock.get_requests()
        pro_req = next(r for r in reqs if r.url.path.endswith("/flux-2-pro"))
        body = _json.loads(pro_req.content)
        assert "image_prompt" in body

        # Cost/model reflect what actually succeeded.
        assert result.model == "flux-2-pro"
        assert result.cost_estimate == "$0.03"

    @pytest.mark.anyio
    async def test_flux_2_max_never_called_with_refs(self, httpx_mock):
        from mcp_bildsprache.providers.bfl import generate_bfl

        # Both reference-capable endpoints fail — we expect the function to
        # raise WITHOUT ever trying flux-2-max (text-only).
        httpx_mock.add_response(
            url="https://api.bfl.ai/v1/flux-kontext-pro",
            status_code=500,
        )
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

        assert result.model == "recraft-v4"


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
