"""Tests for providers/bfl.py — FLUX 3 edit path.

Covers: prompt composition (tags, keep clause, region rows, BFL box order),
resolution/aspect mapping, the submit → poll → download flow, failure
statuses, 429 backoff, and the missing-key guard.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from pydantic import SecretStr
from pytest_httpx import HTTPXMock

from mcp_bildsprache.providers import bfl
from mcp_bildsprache.providers.bfl import (
    FLUX3_URL,
    BFLError,
    Region,
    closest_aspect_ratio,
    compose_edit_prompt,
    edit_flux3,
    resolution_for,
)

POLL_URL = "https://api.bfl.ai/v1/get_result?id=job-1"
SAMPLE_URL = "https://delivery.bfl.ai/sample.jpeg"


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_bildsprache.config import settings

    monkeypatch.setattr(settings, "bfl_api_key", SecretStr("bfl-test-key"))
    monkeypatch.setattr(bfl, "_POLL_INTERVAL_S", 0)


def _rows(prompt: str) -> list[dict]:
    return json.loads(prompt.splitlines()[-1])


class TestComposeEditPrompt:
    def test_plain_instruction_gets_ref_and_keep_clause(self) -> None:
        prompt = compose_edit_prompt("Make the mug green")
        assert prompt.splitlines() == [
            "In <ref_image_0>, make the mug green",
            "Keep the rest of the image exactly unchanged.",
        ]

    def test_existing_ref_prefix_is_not_doubled(self) -> None:
        prompt = compose_edit_prompt("In <ref_image_0>, make the mug green")
        assert prompt.count("<ref_image_0>") == 1

    def test_edit_region_row_uses_bfl_box_order(self) -> None:
        region = Region(id="mug_1", box=(100, 200, 300, 400), desc="a green mug")
        rows = _rows(compose_edit_prompt("Make <mug_1> green", [region]))
        # [left, top, right, bottom] → BFL [y0, x0, y1, x1]
        assert rows == [{
            "id": "mug_1", "from": None, "src_bbox": None,
            "tgt_bbox": [200, 100, 400, 300], "desc": "a green mug", "kind": "new",
        }]

    def test_keep_and_move_rows(self) -> None:
        keep = Region(id="sofa_1", box=(0, 500, 600, 1000), desc="grey sofa", kind="keep")
        move = Region(id="dog_1", box=(600, 600, 900, 900), desc="dog", kind="move",
                      to=(100, 600, 400, 900))
        rows = _rows(compose_edit_prompt("Move <dog_1> left", [keep, move]))
        assert rows[0]["kind"] == "anchor"
        assert rows[0]["from"] == "ref_image_0"
        assert rows[0]["src_bbox"] == rows[0]["tgt_bbox"] == [500, 0, 1000, 600]
        assert rows[1]["kind"] == "move"
        assert rows[1]["src_bbox"] == [600, 600, 900, 900]
        assert rows[1]["tgt_bbox"] == [600, 100, 900, 400]

    def test_untagged_regions_are_named(self) -> None:
        region = Region(id="lamp_1", box=(0, 0, 100, 100), desc="the floor lamp")
        prompt = compose_edit_prompt("Remove the lamp", [region])
        assert "Regions: <lamp_1> the floor lamp." in prompt

    def test_move_without_target_is_rejected(self) -> None:
        region = Region(id="dog_1", box=(0, 0, 10, 10), desc="dog", kind="move")
        with pytest.raises(ValueError, match="needs 'to'"):
            compose_edit_prompt("Move <dog_1>", [region])


class TestSizing:
    @pytest.mark.parametrize(
        ("w", "h", "tier"),
        [(1024, 1024, "1k"), (1200, 628, "1k"), (1600, 900, "2k"), (2048, 2048, "2k"), (3840, 2160, "4k")],
    )
    def test_resolution_tier_covers_area(self, w: int, h: int, tier: str) -> None:
        assert resolution_for(w, h) == tier

    @pytest.mark.parametrize(
        ("w", "h", "token"),
        [(1600, 900, "16:9"), (1080, 1350, "4:5"), (1200, 628, "2:1"), (1000, 1000, "1:1")],
    )
    def test_closest_aspect_ratio(self, w: int, h: int, token: str) -> None:
        assert closest_aspect_ratio(w, h) == token


def _ok_flow(httpx_mock: HTTPXMock, *, pending: int = 1) -> None:
    httpx_mock.add_response(method="POST", url=FLUX3_URL, json={"id": "job-1", "polling_url": POLL_URL})
    for _ in range(pending):
        httpx_mock.add_response(method="GET", url=POLL_URL, json={"id": "job-1", "status": "Pending"})
    httpx_mock.add_response(
        method="GET",
        url=POLL_URL,
        json={"id": "job-1", "status": "Ready", "result": {"sample": SAMPLE_URL, "prompt": "expanded"}},
    )
    httpx_mock.add_response(method="GET", url=SAMPLE_URL, content=b"edited", headers={"content-type": "image/jpeg"})


class TestEditFlux3:
    async def test_bytes_source_round_trip(self, httpx_mock: HTTPXMock) -> None:
        _ok_flow(httpx_mock)
        result = await edit_flux3("In <ref_image_0>, x", b"source-bytes", width=1600, height=900)

        submit = httpx_mock.get_requests(method="POST")[0]
        body = json.loads(submit.content)
        assert submit.headers["x-key"] == "bfl-test-key"
        assert body == {
            "prompt": "In <ref_image_0>, x",
            "images": [base64.b64encode(b"source-bytes").decode()],
            "aspect_ratio": "16:9",
            "resolution": "2k",
            "grounding": False,
        }
        assert result.image_data == b"edited"
        assert result.mime_type == "image/jpeg"
        assert result.model == "flux-3-image"
        assert result.cost_estimate == "$0.1"
        assert result.cost_model == "flux-3-image-2k"
        assert result.revised_prompt == "expanded"

    async def test_url_source_without_size_keeps_framing(self, httpx_mock: HTTPXMock) -> None:
        _ok_flow(httpx_mock, pending=0)
        await edit_flux3("p", "https://example.com/a.jpg")
        body = json.loads(httpx_mock.get_requests(method="POST")[0].content)
        assert body["images"] == ["https://example.com/a.jpg"]
        assert body["aspect_ratio"] == "auto"
        assert body["resolution"] == "1k"

    @pytest.mark.parametrize("status", ["Content Moderated", "Request Moderated", "Error"])
    async def test_failed_status_raises(self, httpx_mock: HTTPXMock, status: str) -> None:
        httpx_mock.add_response(method="POST", url=FLUX3_URL, json={"id": "job-1", "polling_url": POLL_URL})
        httpx_mock.add_response(method="GET", url=POLL_URL, json={"status": status})
        with pytest.raises(BFLError, match=status):
            await edit_flux3("p", b"x")

    async def test_validation_error_body_is_surfaced(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(method="POST", url=FLUX3_URL, status_code=422, json={"detail": "bad prompt"})
        with pytest.raises(httpx.HTTPStatusError, match="bad prompt"):
            await edit_flux3("p", b"x")

    async def test_429_is_retried(self, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _no_sleep(_s: float) -> None:
            return None

        monkeypatch.setattr(bfl.asyncio, "sleep", _no_sleep)
        httpx_mock.add_response(method="POST", url=FLUX3_URL, status_code=429)
        _ok_flow(httpx_mock, pending=0)
        result = await edit_flux3("p", b"x")
        assert result.image_data == b"edited"

    async def test_missing_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_bildsprache.config import settings

        monkeypatch.setattr(settings, "bfl_api_key", SecretStr(""))
        with pytest.raises(BFLError, match="BFL_API_KEY"):
            await edit_flux3("p", b"x")


async def test_webp_source_is_sent_as_png(httpx_mock: HTTPXMock) -> None:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 16), color=(1, 2, 3)).save(buf, format="WEBP")
    _ok_flow(httpx_mock, pending=0)
    await edit_flux3("p", buf.getvalue())
    sent = base64.b64decode(json.loads(httpx_mock.get_requests(method="POST")[0].content)["images"][0])
    with Image.open(io.BytesIO(sent)) as img:
        assert img.format == "PNG"
        assert img.size == (32, 16)
