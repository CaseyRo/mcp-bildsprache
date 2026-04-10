"""Black Forest Labs FLUX image generation provider."""

from __future__ import annotations

import asyncio
import logging

import httpx

from mcp_bildsprache.config import settings
from mcp_bildsprache.types import ProviderResult

logger = logging.getLogger(__name__)

# FLUX model endpoints and metadata
FLUX_MODELS = {
    "flux-2-max": {
        "url": "https://api.bfl.ai/v1/flux-2-max",
        "cost": "$0.07",
        "snap": 16,
        "max_mp": 4.0,
    },
    "flux-2-pro": {
        "url": "https://api.bfl.ai/v1/flux-2-pro",
        "cost": "$0.03",
        "snap": 16,
        "max_mp": 4.0,
    },
    "flux-kontext-pro": {
        "url": "https://api.bfl.ai/v1/flux-kontext-pro",
        "cost": "$0.04",
        "snap": 32,
        "max_mp": 1.0,
    },
    "flux-pro-1.1": {
        "url": "https://api.bfl.ai/v1/flux-pro-1.1",
        "cost": "$0.04",
        "snap": 32,
        "max_mp": 1.44 * 1.44,  # 1440x1440 max
    },
}

DEFAULT_MODEL = "flux-2-max"
FALLBACK_CHAIN = ["flux-2-max", "flux-2-pro", "flux-pro-1.1"]

BFL_RESULT_URL = "https://api.bfl.ai/v1/get_result"


def _snap_dimensions(width: int, height: int, snap: int, max_mp: float) -> tuple[int, int]:
    """Snap dimensions to grid and enforce max megapixel limit."""
    w = max(snap, round(width / snap) * snap)
    h = max(snap, round(height / snap) * snap)

    # Enforce max megapixel limit
    mp = (w * h) / 1_000_000
    if mp > max_mp:
        scale = (max_mp / mp) ** 0.5
        w = max(snap, round(w * scale / snap) * snap)
        h = max(snap, round(h * scale / snap) * snap)

    return w, h


async def generate_bfl(
    prompt: str,
    width: int = 1600,
    height: int = 900,
    model: str | None = None,
) -> ProviderResult:
    """Generate an image using FLUX via BFL API.

    Uses FLUX.2 Max by default. Falls back through the model chain on failure.
    Downloads the image and returns a ProviderResult with raw bytes.
    """
    api_key = settings.bfl_api_key.get_secret_value()
    if not api_key:
        raise ValueError("BFL_API_KEY not configured")

    selected = model or DEFAULT_MODEL
    models_to_try = [selected] + [m for m in FALLBACK_CHAIN if m != selected]

    last_error = None
    for model_id in models_to_try:
        model_info = FLUX_MODELS.get(model_id)
        if not model_info:
            continue
        try:
            return await _generate_with_model(api_key, model_id, model_info, prompt, width, height)
        except Exception as e:
            logger.warning("BFL model %s failed: %s — trying next", model_id, e)
            last_error = e

    raise last_error or ValueError("All BFL models failed")


async def _generate_with_model(
    api_key: str,
    model_id: str,
    model_info: dict,
    prompt: str,
    width: int,
    height: int,
) -> ProviderResult:
    """Generate with a specific FLUX model."""
    w, h = _snap_dimensions(width, height, model_info["snap"], model_info["max_mp"])

    headers = {
        "Content-Type": "application/json",
        "x-key": api_key,
    }

    payload = {
        "prompt": prompt,
        "width": w,
        "height": h,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        submit_response = await client.post(model_info["url"], json=payload, headers=headers)
        submit_response.raise_for_status()
        job = submit_response.json()

        task_id = job.get("id")
        polling_url = job.get("polling_url", f"{BFL_RESULT_URL}?id={task_id}")

        if not task_id:
            raise ValueError(f"BFL ({model_id}) returned no task ID")

        # Poll for result (max 90 seconds — FLUX.2 Max can be slower)
        for _ in range(45):
            await asyncio.sleep(2)
            result_response = await client.get(polling_url, headers=headers)
            result_response.raise_for_status()
            result = result_response.json()

            status = result.get("status")
            if status == "Ready":
                image_url = result.get("result", {}).get("sample")
                if not image_url:
                    raise ValueError(f"BFL ({model_id}) returned Ready but no image URL")

                img_response = await client.get(image_url)
                img_response.raise_for_status()
                content_type = img_response.headers.get("content-type", "image/jpeg")

                return ProviderResult(
                    image_data=img_response.content,
                    mime_type=content_type,
                    model=model_id,
                    cost_estimate=model_info["cost"],
                )

            if status in ("Error", "Failed"):
                raise ValueError(f"BFL ({model_id}) generation failed: {result}")

    raise TimeoutError(f"BFL ({model_id}) generation timed out after 90 seconds")
