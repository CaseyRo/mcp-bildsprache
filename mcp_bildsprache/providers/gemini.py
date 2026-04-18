"""Gemini (Nano Banana 2) image generation provider."""

from __future__ import annotations

import base64
import io
import logging

import httpx
from PIL import Image, UnidentifiedImageError

from mcp_bildsprache.config import settings
from mcp_bildsprache.types import ProviderResult

logger = logging.getLogger(__name__)

# Ordered by preference — first available model wins.
# Update this list when Google releases new image generation models.
GEMINI_MODELS = [
    "gemini-3.1-flash-image-preview",  # Nano Banana 2 (best, preview)
    "gemini-2.5-flash-image",           # Stable fallback
]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Mapping of Pillow "format" strings to the mime types Gemini's inlineData
# parts accept. Anything outside this set → ValueError.
_PILLOW_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def _probe_mime(index: int, data: bytes) -> str:
    """Return the image mime type for ``data`` or raise ValueError naming
    the offending list index.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(
            f"reference_images[{index}]: could not identify image format ({exc})"
        ) from exc
    mime = _PILLOW_FORMAT_TO_MIME.get(fmt or "")
    if mime is None:
        raise ValueError(
            f"reference_images[{index}]: unsupported image format {fmt!r} "
            f"(expected JPEG, PNG, or WEBP)"
        )
    return mime


async def generate_gemini(
    prompt: str,
    width: int = 1200,
    height: int = 1200,
    reference_images: list[bytes] | None = None,
) -> ProviderResult:
    """Generate an image using Gemini's multimodal generation.

    Tries Nano Banana 2 first, falls back to gemini-2.5-flash-image if unavailable.
    Returns a ProviderResult with decoded image bytes.

    If ``reference_images`` is provided and non-empty, each blob is appended
    as an additional ``inlineData`` part to ``contents[0].parts`` alongside
    the text prompt. Mime types are probed via Pillow; anything that is not
    JPEG/PNG/WEBP raises ``ValueError`` naming the offending list index.
    """
    api_key = settings.gemini_api_key.get_secret_value()
    if not api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    # Probe mime types up-front so we fail fast with a clear error before
    # any HTTP call.
    if reference_images:
        probed = [(_probe_mime(i, b), b) for i, b in enumerate(reference_images)]
    else:
        probed = []

    last_error = None
    for model in GEMINI_MODELS:
        try:
            return await _generate_with_model(api_key, model, prompt, width, height, probed)
        except Exception as e:
            logger.warning("Gemini model %s failed: %s — trying next", model, e)
            last_error = e

    raise last_error or ValueError("All Gemini models failed")


async def _generate_with_model(
    api_key: str,
    model: str,
    prompt: str,
    width: int,
    height: int,
    reference_parts: list[tuple[str, bytes]],
) -> ProviderResult:
    """Generate with a specific Gemini model."""
    url = f"{GEMINI_URL}/{model}:generateContent?key={api_key}"

    parts: list[dict] = [
        {
            "text": (
                f"Generate an image based on this description. "
                f"Target dimensions: {width}x{height}. "
                f"Description: {prompt}"
            ),
        }
    ]
    for mime, data in reference_parts:
        parts.append(
            {
                "inlineData": {
                    "mimeType": mime,
                    "data": base64.b64encode(data).decode("ascii"),
                }
            }
        )

    payload = {
        "contents": [{"parts": parts}],
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
            image_bytes = base64.b64decode(part["inlineData"]["data"])
            return ProviderResult(
                image_data=image_bytes,
                mime_type=part["inlineData"]["mimeType"],
                model=model,
                cost_estimate="$0.01",
            )

    raise ValueError(f"Gemini ({model}) returned no image data in response")
