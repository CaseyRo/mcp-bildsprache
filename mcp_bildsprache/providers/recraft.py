"""Recraft V3 image generation provider."""

from __future__ import annotations

import logging

import httpx

from mcp_bildsprache.config import settings

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
    style: str = "digital_illustration",
) -> dict:
    """Generate an image using Recraft V3.

    Returns dict with 'image_url', 'model', 'cost_estimate', and optional 'license_warning'.
    """
    api_key = settings.recraft_api_key.get_secret_value()
    if not api_key:
        raise ValueError("RECRAFT_API_KEY not configured")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {
        "prompt": prompt,
        "model": "recraftv3",
        "style": style,
        "size": _snap_size(width, height),
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(RECRAFT_API_URL, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    images = data.get("data", [])
    if not images:
        raise ValueError("Recraft returned no images")

    result = {
        "image_url": images[0].get("url", ""),
        "image_id": images[0].get("image_id", ""),
        "model": "recraft-v3",
        "cost_estimate": "1-2 credits (free tier)",
    }

    # Add license warning for free tier
    if settings.recraft_tier == "free":
        result["license_warning"] = (
            "Free tier output — no commercial license. "
            "Upgrade to Recraft Pro ($48/mo) for commercial use."
        )

    return result
