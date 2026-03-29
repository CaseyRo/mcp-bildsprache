"""Gemini (Nano Banana 2) image generation provider."""

from __future__ import annotations

import base64
import logging

import httpx

from mcp_bildsprache.config import settings

logger = logging.getLogger(__name__)

GEMINI_MODELS = [
    "gemini-3.1-flash-image-preview",  # Nano Banana 2 (best, preview)
    "gemini-2.5-flash-image",           # Stable fallback
]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"


async def generate_gemini(prompt: str, width: int = 1200, height: int = 1200) -> dict:
    """Generate an image using Gemini's multimodal generation.

    Tries Nano Banana 2 first, falls back to gemini-2.5-flash-image if unavailable.
    Returns dict with 'image_base64', 'mime_type', and 'model'.
    """
    api_key = settings.gemini_api_key.get_secret_value()
    if not api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    last_error = None
    for model in GEMINI_MODELS:
        try:
            return await _generate_with_model(api_key, model, prompt, width, height)
        except Exception as e:
            logger.warning("Gemini model %s failed: %s — trying next", model, e)
            last_error = e

    raise last_error or ValueError("All Gemini models failed")


async def _generate_with_model(
    api_key: str, model: str, prompt: str, width: int, height: int
) -> dict:
    """Generate with a specific Gemini model."""
    url = f"{GEMINI_URL}/{model}:generateContent?key={api_key}"

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            f"Generate an image based on this description. "
                            f"Target dimensions: {width}x{height}. "
                            f"Description: {prompt}"
                        ),
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

    # Extract image from response
    candidates = data.get("candidates", [])
    if not candidates:
        raise ValueError(f"Gemini ({model}) returned no candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
    for part in parts:
        if "inlineData" in part:
            return {
                "image_base64": part["inlineData"]["data"],
                "mime_type": part["inlineData"]["mimeType"],
                "model": model,
                "cost_estimate": "$0.01",
            }

    raise ValueError(f"Gemini ({model}) returned no image data in response")
