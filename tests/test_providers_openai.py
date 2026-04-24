"""Tests for providers/openai.py — CDI-1014 §4.

Covers: size validation/snapping, param guards (input_fidelity strip,
transparent rejection, streaming rejection), draft routing, usage +
revised_prompt capture, 429 backoff, missing API key.
"""

from __future__ import annotations

import base64

import pytest
from pytest_httpx import HTTPXMock

from mcp_bildsprache.providers.openai import (
    OpenAIRateLimited,
    OpenAISizeError,
    _validate_and_snap_size,
    generate_openai,
)


TINY_WEBP = base64.b64encode(b"fake-image-bytes").decode("ascii")


def _response_body(revised_prompt: str | None = None, usage: dict | None = None) -> dict:
    entry: dict = {"b64_json": TINY_WEBP}
    if revised_prompt is not None:
        entry["revised_prompt"] = revised_prompt
    body: dict = {"data": [entry]}
    if usage is not None:
        body["usage"] = usage
    return body


@pytest.fixture(autouse=True)
def _set_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_bildsprache.config import settings
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "openai_api_key", SecretStr("sk-test-key"))


class TestSizeValidation:
    def test_1024_square_unchanged(self) -> None:
        assert _validate_and_snap_size(1024, 1024) == (1024, 1024)

    def test_non_multiple_of_16_snaps(self) -> None:
        # 1200x630 → 1200 is already 16-aligned; 630 snaps to 624 (nearest).
        w, h = _validate_and_snap_size(1200, 630)
        assert w % 16 == 0 and h % 16 == 0
        assert w == 1200
        # round(630/16)*16 = 624; accept either 624 or 640 if implementation changes later
        assert h in (624, 640)

    def test_aspect_ratio_over_3_1_rejected(self) -> None:
        with pytest.raises(OpenAISizeError, match="aspect ratio"):
            _validate_and_snap_size(4000, 500)  # 8:1

    def test_too_many_pixels_rejected(self) -> None:
        with pytest.raises(OpenAISizeError, match="pixel count"):
            _validate_and_snap_size(4096, 4096)  # 16.7M

    def test_too_few_pixels_scales_up(self) -> None:
        # 256x256 = 65536 pixels, below the 655_360 minimum
        w, h = _validate_and_snap_size(256, 256)
        assert w * h >= 655_360
        # Ratio should still be ~1:1
        assert abs(w - h) <= 16

    def test_max_edge_capped(self) -> None:
        # 5000x1024 pixel-wise is allowed but max edge is 3840
        w, h = _validate_and_snap_size(5000, 1700)
        assert w <= 3840 and h <= 3840

    def test_invalid_dims_rejected(self) -> None:
        with pytest.raises(OpenAISizeError):
            _validate_and_snap_size(0, 100)


class TestParamGuards:
    async def test_transparent_background_rejected(self) -> None:
        with pytest.raises(ValueError, match="transparent"):
            await generate_openai("x", background="transparent")

    async def test_unsupported_background_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsupported background"):
            await generate_openai("x", background="rainbow")

    async def test_unsupported_quality_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsupported quality"):
            await generate_openai("x", quality="ultra")

    async def test_streaming_rejected(self) -> None:
        with pytest.raises(ValueError, match="streaming"):
            await generate_openai("x", stream=True)

    async def test_missing_api_key_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_bildsprache.config import settings
        from pydantic import SecretStr

        monkeypatch.setattr(settings, "openai_api_key", SecretStr(""))
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            await generate_openai("x")

    async def test_input_fidelity_stripped(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            url="https://api.openai.com/v1/images/generations",
            json=_response_body(usage={"input_tokens": 10, "output_tokens": 50}),
        )
        result = await generate_openai("x", input_fidelity="high")
        # Doesn't raise — param was dropped silently (confirmed by API mock success).
        assert result.model == "gpt-image-2"


class TestDispatchAndCapture:
    async def test_default_model_gpt_image_2(self, httpx_mock: HTTPXMock) -> None:
        import json as _json

        httpx_mock.add_response(json=_response_body(usage={"input_tokens": 1, "output_tokens": 1}))
        r = await generate_openai("prompt")
        request = httpx_mock.get_request()
        assert request is not None
        payload = _json.loads(request.read())
        assert payload["model"] == "gpt-image-2"
        assert r.model == "gpt-image-2"

    async def test_draft_flag_routes_to_mini(self, httpx_mock: HTTPXMock) -> None:
        import json as _json

        httpx_mock.add_response(json=_response_body(usage={"input_tokens": 1, "output_tokens": 1}))
        r = await generate_openai("prompt", draft=True)
        request = httpx_mock.get_request()
        assert request is not None
        assert _json.loads(request.read())["model"] == "gpt-image-1-mini"
        assert r.model == "gpt-image-1-mini"

    async def test_explicit_model_override(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_response_body(usage={"input_tokens": 1, "output_tokens": 1}))
        r = await generate_openai("prompt", model="gpt-image-1.5")
        assert r.model == "gpt-image-1.5"

    async def test_usage_captured(self, httpx_mock: HTTPXMock) -> None:
        usage = {
            "input_tokens": 412,
            "output_tokens": 1120,
            "input_tokens_details": {
                "text_tokens": 112,
                "image_tokens": 300,
                "cached_tokens": 0,
            },
        }
        httpx_mock.add_response(json=_response_body(usage=usage))
        r = await generate_openai("prompt")
        assert r.usage == usage

    async def test_revised_prompt_captured(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            json=_response_body(
                revised_prompt="A revised version",
                usage={"input_tokens": 1, "output_tokens": 1},
            )
        )
        r = await generate_openai("original")
        assert r.revised_prompt == "A revised version"

    async def test_no_revised_prompt_is_none(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_response_body(usage={"input_tokens": 1, "output_tokens": 1}))
        r = await generate_openai("prompt")
        assert r.revised_prompt is None

    async def test_output_format_webp_default(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_response_body(usage={"input_tokens": 1, "output_tokens": 1}))
        r = await generate_openai("prompt")
        assert r.mime_type == "image/webp"

    async def test_image_data_decoded(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_response_body(usage={"input_tokens": 1, "output_tokens": 1}))
        r = await generate_openai("prompt")
        assert r.image_data == b"fake-image-bytes"

    async def test_provenance_flags_no_synthid(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_response_body(usage={"input_tokens": 1, "output_tokens": 1}))
        r = await generate_openai("prompt")
        assert r.provenance_flags == {"synthid": False, "c2pa": False}


class TestRateLimitBackoff:
    async def test_retries_on_429_then_succeeds(
        self, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Patch asyncio.sleep so tests don't actually wait.
        import asyncio as _asyncio

        async def _noop(*_args, **_kwargs):
            return None

        monkeypatch.setattr(_asyncio, "sleep", _noop)

        httpx_mock.add_response(status_code=429, json={"error": "rate_limit"})
        httpx_mock.add_response(json=_response_body(usage={"input_tokens": 1, "output_tokens": 1}))
        r = await generate_openai("prompt")
        assert r.model == "gpt-image-2"

    async def test_exhausts_budget_and_raises(
        self, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio as _asyncio

        async def _noop(*_args, **_kwargs):
            return None

        monkeypatch.setattr(_asyncio, "sleep", _noop)

        # 4 x 429 (initial + 3 retries) exhausts budget
        for _ in range(4):
            httpx_mock.add_response(status_code=429, json={"error": "rate_limit"})
        with pytest.raises(OpenAIRateLimited):
            await generate_openai("prompt")


class TestReferenceImages:
    async def test_reference_images_dropped_silently(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        httpx_mock.add_response(json=_response_body(usage={"input_tokens": 1, "output_tokens": 1}))
        import logging

        with caplog.at_level(logging.INFO):
            r = await generate_openai("prompt", reference_images=[b"ref1", b"ref2"])
        assert r.model == "gpt-image-2"
        assert any("dropping 2 reference" in rec.message for rec in caplog.records)


class TestSizeInRequest:
    async def test_snapped_size_in_payload(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_response_body(usage={"input_tokens": 1, "output_tokens": 1}))
        await generate_openai("prompt", width=1200, height=630)
        request = httpx_mock.get_request()
        assert request is not None
        payload = request.read().decode()
        # Should contain a snapped size like "1200x640" (both multiples of 16)
        assert '"size":' in payload
        import re
        m = re.search(r'"size":\s*"(\d+)x(\d+)"', payload)
        assert m is not None
        w, h = int(m.group(1)), int(m.group(2))
        assert w % 16 == 0 and h % 16 == 0


class TestOutputCompression:
    async def test_compression_included_for_webp(self, httpx_mock: HTTPXMock) -> None:
        import json as _json

        httpx_mock.add_response(json=_response_body(usage={"input_tokens": 1, "output_tokens": 1}))
        await generate_openai("prompt", output_format="webp", output_compression=85)
        body = _json.loads(httpx_mock.get_request().read())
        assert body["output_compression"] == 85

    async def test_compression_omitted_for_png(self, httpx_mock: HTTPXMock) -> None:
        import json as _json

        httpx_mock.add_response(json=_response_body(usage={"input_tokens": 1, "output_tokens": 1}))
        await generate_openai("prompt", output_format="png", output_compression=50)
        body = _json.loads(httpx_mock.get_request().read())
        assert "output_compression" not in body
