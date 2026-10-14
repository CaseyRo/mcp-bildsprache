"""Black Forest Labs FLUX 3 Image provider — edit path only.

Backs ``edit_image``: one source image plus an instruction (optionally with
bounding-box regions) in, an edited image out. ``generate_image`` does not
dispatch here; FLUX hints there are still rejected by ``route_model``.

API (docs.bfl.ai, FLUX 3 Image, launched 2026-10-01): ``POST /v1/flux-3-image``
with an ``x-key`` header returns ``{id, polling_url}``; poll until ``status``
is ``Ready`` and download ``result.sample`` (signed, expires after 1 hour).
There is no width/height, seed or output format: size comes from an
``aspect_ratio`` token plus an equal-area ``resolution`` tier, and the
pipeline crops to the caller's exact dimensions afterwards.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import random
import time
from dataclasses import dataclass
from typing import Literal

import httpx
from PIL import Image, UnidentifiedImageError

from mcp_bildsprache.config import settings
from mcp_bildsprache.types import ProviderResult

logger = logging.getLogger(__name__)

FLUX3_MODEL = "flux-3-image"
FLUX3_URL = "https://api.bfl.ai/v1/flux-3-image"

# USD per image by resolution tier (BFL pricing page, 2026-10-12). The shared
# cost table prices each tier as its own row (``cost_model_for``); this copy
# only feeds the legacy "$x" cost string. ``1.5k`` is unpriced on the pricing
# page, so it is never chosen.
RESOLUTION_USD: dict[str, float] = {"1k": 0.048, "2k": 0.100, "4k": 0.607}

# Output aspect ratios FLUX 3 accepts (besides "auto").
_ASPECT_RATIOS: dict[str, float] = {
    token: int(token.split(":")[0]) / int(token.split(":")[1])
    for token in (
        "21:9", "2:1", "16:9", "3:2", "7:5", "4:3", "5:4", "1:1",
        "4:5", "3:4", "5:7", "2:3", "9:16", "1:2", "9:21",
    )
}

_POLL_INTERVAL_S = 1.5
# Edits run on a detached job (CDI-1266), so the portal budget doesn't apply;
# BFL warns 4K can take several minutes.
_POLL_TIMEOUT_S = 300.0
_FAILED_STATUSES = ("Error", "Failed", "Request Moderated", "Content Moderated", "Task not found")


class BFLError(Exception):
    """FLUX 3 refused or failed the request."""


RegionKind = Literal["edit", "keep", "move", "add"]


@dataclass(frozen=True, slots=True)
class Region:
    """One bounding box on the 0-1000 grid, as ``[left, top, right, bottom]``.

    ``edit`` and ``add`` render something new in ``box``; ``keep`` pins an
    element in place; ``move`` moves the element in ``box`` to ``to``.
    """

    id: str
    box: tuple[int, int, int, int]
    desc: str
    kind: RegionKind = "edit"
    to: tuple[int, int, int, int] | None = None


def _bfl_box(box: tuple[int, int, int, int]) -> list[int]:
    """``[left, top, right, bottom]`` → BFL's ``[y0, x0, y1, x1]``.

    BFL's own helper (flux3_image_layout, ``to_bbox``) returns top, left,
    bottom, right. Keeping the conversion here means callers think in x-first
    boxes and only this function knows BFL's order.
    """
    left, top, right, bottom = box
    return [top, left, bottom, right]


def _row(region: Region) -> dict:
    if region.kind == "keep":
        src = tgt = _bfl_box(region.box)
        return {"id": region.id, "from": "ref_image_0", "src_bbox": src, "tgt_bbox": tgt,
                "desc": region.desc, "kind": "anchor"}
    if region.kind == "move":
        if region.to is None:
            raise ValueError(f"region {region.id!r}: kind 'move' needs 'to'")
        return {"id": region.id, "from": "ref_image_0", "src_bbox": _bfl_box(region.box),
                "tgt_bbox": _bfl_box(region.to), "desc": region.desc, "kind": "move"}
    # edit / add: render something new in the box.
    return {"id": region.id, "from": None, "src_bbox": None, "tgt_bbox": _bfl_box(region.box),
            "desc": region.desc, "kind": "new"}


def compose_edit_prompt(instruction: str, regions: list[Region] | None = None) -> str:
    """Build the FLUX 3 edit prompt: instruction, keep-clause, then region rows.

    BFL has no field for boxes; they go into the prompt as ``<id>`` tags plus a
    JSON array of rows. Regions the instruction doesn't tag are named after it
    so every row is referenced.
    """
    text = instruction.strip()
    if not text.lower().startswith("in <ref_image_0>"):
        text = f"In <ref_image_0>, {text[:1].lower()}{text[1:]}"
    lines = [text]
    if regions:
        untagged = [r for r in regions if f"<{r.id}>" not in text]
        if untagged:
            lines.append("Regions: " + "; ".join(f"<{r.id}> {r.desc}" for r in untagged) + ".")
    lines.append("Keep the rest of the image exactly unchanged.")
    if regions:
        lines.append(json.dumps([_row(r) for r in regions]))
    return "\n".join(lines)


def resolution_for(width: int, height: int) -> str:
    """Smallest priced tier whose pixel area covers ``width x height``.

    Tiers are equal-area: 1k ≈ 1 MP, 2k ≈ 4 MP, 4k ≈ 16 MP. The pipeline
    downsizes to the exact target, so rounding up keeps the result sharp.
    """
    mp = width * height / 1_000_000
    if mp <= 1.1:
        return "1k"
    if mp <= 4.2:
        return "2k"
    return "4k"


def closest_aspect_ratio(width: int, height: int) -> str:
    """The FLUX 3 aspect-ratio token nearest ``width:height`` (log-space)."""
    target = math.log(width / height)
    return min(_ASPECT_RATIOS, key=lambda t: abs(target - math.log(_ASPECT_RATIOS[t])))


def cost_model_for(resolution: str) -> str:
    """Cost-table row for a resolution tier (``bfl.flux-3-image-<tier>``)."""
    return f"{FLUX3_MODEL}-{resolution}"


async def edit_flux3(
    prompt: str,
    image: str | bytes,
    *,
    width: int | None = None,
    height: int | None = None,
) -> ProviderResult:
    """Edit ``image`` with FLUX 3 and return the edited bytes.

    Args:
        prompt: The full edit prompt (see :func:`compose_edit_prompt`).
        image: Source image as bytes (sent base64) or an http(s) URL BFL fetches.
        width, height: Target size. When given, the output ratio is the nearest
            FLUX 3 token and the tier covers the area; when omitted, the output
            keeps the source framing (``aspect_ratio="auto"``) at the 1k tier.
    """
    api_key = settings.bfl_api_key.get_secret_value()
    if not api_key:
        raise BFLError("BFL_API_KEY not configured")

    source = base64.b64encode(_as_jpeg_or_png(image)).decode("ascii") if isinstance(image, bytes) else image
    resolution = resolution_for(width, height) if width and height else "1k"
    payload = {
        "prompt": prompt,
        "images": [source],
        "aspect_ratio": closest_aspect_ratio(width, height) if width and height else "auto",
        "resolution": resolution,
        # Grounding sends the prompt to web/image search; an edit never needs it.
        "grounding": False,
    }
    headers = {"Content-Type": "application/json", "x-key": api_key}

    async with httpx.AsyncClient(timeout=60.0) as client:
        submit = await _post_with_backoff(client, payload, headers)
        job = submit.json()
        polling_url = job.get("polling_url")
        if not polling_url:
            raise BFLError(f"bfl: submit returned no polling_url: {job}")

        deadline = time.monotonic() + _POLL_TIMEOUT_S
        while True:
            await asyncio.sleep(_POLL_INTERVAL_S)
            polled = await client.get(polling_url, headers=headers)
            _raise_for_status_with_body(polled)
            body = polled.json()
            status = body.get("status")
            if status == "Ready":
                break
            if status in _FAILED_STATUSES:
                raise BFLError(f"bfl: {status}: {body.get('details') or body}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"bfl: edit not ready after {_POLL_TIMEOUT_S:.0f}s")

        result = body.get("result") or {}
        sample = result.get("sample")
        if not sample:
            raise BFLError(f"bfl: Ready without result.sample: {body}")
        img = await client.get(sample)
        _raise_for_status_with_body(img)

    usd = RESOLUTION_USD[resolution]
    return ProviderResult(
        image_data=img.content,
        mime_type=img.headers.get("content-type", "image/jpeg").split(";")[0],
        model=FLUX3_MODEL,
        cost_estimate=f"${usd}",
        # FLUX 3 expands the prompt (and may add boxes itself); keep what it ran.
        revised_prompt=result.get("prompt"),
        model_version=FLUX3_MODEL,
        cost_model=cost_model_for(resolution),
        provenance_flags={"synthid": False, "c2pa": False},
    )


def _as_jpeg_or_png(data: bytes) -> bytes:
    """Stored images are WebP; BFL documents JPEG/PNG inputs, so send PNG."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format in ("JPEG", "PNG"):
                return data
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
    except (UnidentifiedImageError, OSError):
        return data  # let BFL reject it with its own message


async def _post_with_backoff(
    client: httpx.AsyncClient, payload: dict, headers: dict[str, str]
) -> httpx.Response:
    """POST the job, retrying 429s (1s/4s/10s + jitter) like the OpenAI provider."""
    for delay in (1.0, 4.0, 10.0, None):
        response = await client.post(FLUX3_URL, json=payload, headers=headers)
        if response.status_code != 429:
            _raise_for_status_with_body(response)
            return response
        if delay is None:
            raise BFLError("bfl: 429 after retry budget exhausted")
        await asyncio.sleep(delay * (0.75 + 0.5 * random.random()))
    raise BFLError("bfl: unreachable backoff exit")  # pragma: no cover


def _raise_for_status_with_body(response: httpx.Response) -> None:
    if response.is_error:
        raise httpx.HTTPStatusError(
            f"bfl: HTTP {response.status_code}: {response.text[:500]}",
            request=response.request,
            response=response,
        )
