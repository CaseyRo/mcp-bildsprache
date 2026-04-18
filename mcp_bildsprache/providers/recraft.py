"""Recraft V4 image generation provider."""

from __future__ import annotations

import logging

import httpx

from mcp_bildsprache.config import settings
from mcp_bildsprache.types import ProviderResult

logger = logging.getLogger(__name__)

RECRAFT_API_URL = "https://external.api.recraft.ai/v1/images/generations"

# Recraft only supports specific sizes — snap to nearest supported
SUPPORTED_SIZES = [
    "1024x1024", "1365x1024", "1024x1365", "1536x1024", "1024x1536",
    "1820x1024", "1024x1820", "1024x2048", "2048x1024", "1434x1024",
    "1024x1434", "1024x1280", "1280x1024",
]


def _snap_size(width: int, height: int) -> str:
    """Snap requested dimensions to nearest Recraft-supported size."""
    target_ratio = width / height
    best = "1024x1024"
    best_diff = float("inf")
    for size in SUPPORTED_SIZES:
        sw, sh = (int(x) for x in size.split("x"))
        diff = abs(sw / sh - target_ratio) + abs(sw * sh - width * height) / 1_000_000
        if diff < best_diff:
            best_diff = diff
            best = size
    return best


async def generate_recraft(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    reference_images: list[bytes] | None = None,
) -> ProviderResult:
    """Generate an image using Recraft V4.

    V4 does not support named style presets — style is prompt-driven.
    Downloads the image and returns a ProviderResult with raw bytes.

    Recraft's text-to-image endpoint does not accept reference images;
    when ``reference_images`` is non-empty they are dropped with a single
    ``INFO`` log and the request proceeds as text-only. Callers relying on
    identity fidelity should prefer a reference-capable provider
    (Gemini or FLUX kontext-pro) — routing is handled upstream in
    ``server.py``.
    """
    api_key = settings.recraft_api_key.get_secret_value()
    if not api_key:
        raise ValueError("RECRAFT_API_KEY not configured")

    if reference_images:
        logger.info(
            "recraft: dropped %d reference image(s) — provider does not support references",
            len(reference_images),
        )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {
        "prompt": prompt,
        "model": "recraftv4",
        "size": _snap_size(width, height),
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(RECRAFT_API_URL, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    images = data.get("data", [])
    if not images:
        raise ValueError("Recraft returned no images")

    image_url = images[0].get("url", "")
    if not image_url:
        raise ValueError("Recraft returned no image URL")

    # Download the image from the temporary URL
    async with httpx.AsyncClient(timeout=30.0) as dl_client:
        img_response = await dl_client.get(image_url)
        img_response.raise_for_status()

    content_type = img_response.headers.get("content-type", "image/png")

    return ProviderResult(
        image_data=img_response.content,
        mime_type=content_type,
        model="recraft-v4",
        cost_estimate="$0.04",
    )
