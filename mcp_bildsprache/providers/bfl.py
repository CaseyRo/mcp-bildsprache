"""Black Forest Labs FLUX.2 Pro image generation provider."""

from __future__ import annotations

import asyncio
import logging

import httpx

from mcp_bildsprache.config import settings

logger = logging.getLogger(__name__)

BFL_API_URL = "https://api.bfl.ai/v1/flux-pro-1.1"
BFL_RESULT_URL = "https://api.bfl.ai/v1/get_result"


def _snap_to_32(value: int) -> int:
    """BFL requires dimensions divisible by 32, between 256 and 1440."""
    snapped = round(value / 32) * 32
    return max(256, min(1440, snapped))


async def generate_bfl(prompt: str, width: int = 1600, height: int = 900) -> dict:
    """Generate an image using FLUX.2 Pro via BFL API.

    BFL uses an async pattern: submit job → poll for result.
    Returns dict with 'image_url', 'model', and 'cost_estimate'.
    """
    api_key = settings.bfl_api_key.get_secret_value()
    if not api_key:
        raise ValueError("BFL_API_KEY not configured")

    # BFL requires dimensions divisible by 32, max 1440
    width = _snap_to_32(width)
    height = _snap_to_32(height)

    headers = {
        "Content-Type": "application/json",
        "x-key": api_key,
    }

    payload = {
        "prompt": prompt,
        "width": width,
        "height": height,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Submit generation job
        submit_response = await client.post(BFL_API_URL, json=payload, headers=headers)
        submit_response.raise_for_status()
        job = submit_response.json()

        task_id = job.get("id")
        polling_url = job.get("polling_url", f"{BFL_RESULT_URL}?id={task_id}")

        if not task_id:
            raise ValueError("BFL returned no task ID")

        # Poll for result (max 60 seconds)
        for _ in range(30):
            await asyncio.sleep(2)
            result_response = await client.get(polling_url, headers=headers)
            result_response.raise_for_status()
            result = result_response.json()

            status = result.get("status")
            if status == "Ready":
                image_url = result.get("result", {}).get("sample")
                if image_url:
                    return {
                        "image_url": image_url,
                        "model": "flux-pro-1.1",
                        "cost_estimate": "$0.04",
                    }
                raise ValueError("BFL returned Ready status but no image URL")

            if status in ("Error", "Failed"):
                raise ValueError(f"BFL generation failed: {result}")

    raise TimeoutError("BFL generation timed out after 60 seconds")
