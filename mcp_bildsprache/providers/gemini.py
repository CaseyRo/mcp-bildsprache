"""Gemini (Nano Banana) image generation provider.

Two active models (model lineup refresh, CDI-1264):

* ``gemini-3.1-flash-image-preview`` (Nano Banana 2) — fast default, the
  raster fallback when OpenAI is unavailable.
* ``gemini-3-pro-image-preview`` (Nano Banana Pro) — top editing/control +
  4K brand graphics; the diagram default. Higher quality, slower, pricier.

``gemini-2.5-flash-image`` was dropped (superseded by Nano Banana 2).
"""

from __future__ import annotations

import base64
import io
import logging
import math

import httpx
from PIL import Image, UnidentifiedImageError

from mcp_bildsprache.config import settings
from mcp_bildsprache.types import ProviderResult

logger = logging.getLogger(__name__)

# Ordered by preference — first available model wins.
# Update this list when Google releases new image generation models.
#
# Nano Banana 2 (flash) leads the default fallback list: it's fast enough to
# fit the MCP portal budget and is the OpenAI-unavailable raster fallback.
# Nano Banana Pro (gemini-3-pro-image-preview) is the higher-quality 4K model
# — the diagram default — selected via the ``model`` override (see
# ``generate_gemini``). It still trails flash in the generic fallback list so
# a Pro failure degrades to the faster model.
GEMINI_MODELS = [
    "gemini-3.1-flash-image-preview",  # Nano Banana 2 (fast default)
    "gemini-3-pro-image-preview",       # Nano Banana Pro (top quality, 4K)
]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Per-model HTTP timeouts (seconds). The MCP portal budgets the whole tool
# call at ~60s. Gemini image models render **native 4K by default** when no
# imageSize is set, which routinely takes 70s+ — past the portal budget — so
# the call times out before the fast 2.5-flash fallback is ever reached
# (CDI-1163). Two defences: (1) constrain imageSize below so renders are
# bounded, and (2) cap the primary attempt tightly so the fast fallback still
# fits inside the portal budget (44s + 14s ≈ 58s < 60s).
_MODEL_TIMEOUTS: dict[str, float] = {
    "gemini-3.1-flash-image-preview": 44.0,
    # Nano Banana Pro is slower (4K-capable). When it leads (diagram path), it
    # gets the larger share of the budget; flash trails it as the fast fallback.
    "gemini-3-pro-image-preview": 44.0,
}
_DEFAULT_TIMEOUT = 30.0

# Supported aspect ratios shared across the active Gemini image models, mapped
# to their numeric width/height ratio. Extreme panoramas (1:4, 4:1, 1:8, 8:1)
# are 3.x-only and skipped here for safety — the post-processing pipeline crops
# to the caller's exact dimensions, so the closest standard ratio is enough.
_GEMINI_ASPECT_RATIOS: dict[str, float] = {
    "1:1": 1.0,
    "2:3": 2 / 3,
    "3:2": 3 / 2,
    "3:4": 3 / 4,
    "4:3": 4 / 3,
    "4:5": 4 / 5,
    "5:4": 5 / 4,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
    "21:9": 21 / 9,
}


def _closest_aspect_ratio(width: int, height: int) -> str:
    """Return the supported Gemini aspect-ratio token closest to ``width:height``.

    Compared in log-space so portrait/landscape are treated symmetrically.
    The downstream pipeline center-crops to the exact requested dimensions, so
    picking the nearest supported ratio (rather than the exact one) is fine.
    """
    if width <= 0 or height <= 0:
        return "1:1"
    target = math.log(width / height)
    return min(
        _GEMINI_ASPECT_RATIOS,
        key=lambda token: abs(target - math.log(_GEMINI_ASPECT_RATIOS[token])),
    )


def _image_size_for(width: int, height: int, model: str = "") -> str:
    """Pick the smallest Gemini imageSize tier whose long edge covers the target.

    Only the 3.x models honour imageSize. We cap the flash model at 2K: it
    carries the same output-token cost as 1K on 3.1 (≈1120 tokens) yet yields
    far more pixels, while 4K (≈2520 tokens) is what blows the flash request
    budget. The pipeline downsizes from 2K to the caller's dimensions (sharp)
    instead of upscaling (soft). 1K's long edge is ~1264px, so any target
    wider/taller than that uses 2K.

    Nano Banana Pro (``gemini-3-pro-image-preview``) is the 4K brand-graphics
    model (model lineup refresh, CDI-1264): for targets beyond ~2K it is
    allowed to render at 4K (its higher per-image token budget is the whole
    point of choosing it). It carries a longer timeout to suit (see
    ``_MODEL_TIMEOUTS``).
    """
    longest = max(width, height)
    if longest <= 1264:
        return "1K"
    if model.startswith("gemini-3-pro") and longest > 2048:
        return "4K"
    return "2K"

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
    *,
    model: str | None = None,
) -> ProviderResult:
    """Generate an image using Gemini's multimodal generation.

    By default tries Nano Banana 2 (``gemini-3.1-flash-image-preview``) first,
    then Nano Banana Pro (``gemini-3-pro-image-preview``) if it fails. Pass
    ``model`` to put a specific model at the front of the attempt order — the
    diagram path uses this to prefer Nano Banana Pro (top editing/control + 4K)
    while keeping the remaining models as fallbacks. An unknown ``model`` is
    attempted as-is, then the standard list. Returns a ProviderResult with
    decoded image bytes.

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

    # Build the attempt order: preferred model first (deduped), then the
    # standard fallback list.
    attempt_order: list[str] = list(GEMINI_MODELS)
    if model:
        attempt_order = [model] + [m for m in GEMINI_MODELS if m != model]

    last_error = None
    for model in attempt_order:
        timeout = _MODEL_TIMEOUTS.get(model, _DEFAULT_TIMEOUT)
        try:
            return await _generate_with_model(
                api_key, model, prompt, width, height, probed, timeout=timeout
            )
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
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> ProviderResult:
    """Generate with a specific Gemini model."""
    # Auth via header, not the URL query string: a non-2xx response raises an
    # httpx error whose message includes the request URL, so a `?key=` would
    # leak the API key into error text surfaced to the MCP client (CDI-1163).
    url = f"{GEMINI_URL}/{model}:generateContent"
    headers = {"x-goog-api-key": api_key}

    parts: list[dict] = [
        {
            "text": (
                f"Generate an image based on this description. "
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

    # Constrain the render shape/size. Without this, the 3.x models default to
    # native 4K, which exceeds the MCP portal request budget (CDI-1163). We set
    # the closest supported aspect ratio always, and imageSize only for the 3.x
    # models — 2.5-flash is fixed ~1024px and rejects/ignores imageSize.
    #
    # For the :generateContent endpoint the field is generationConfig.imageConfig
    # (camelCase, keys directly inside). The `responseFormat.image` shape only
    # applies to the separate /v1beta/interactions endpoint and is rejected here
    # with HTTP 400.
    image_config: dict[str, str] = {
        "aspectRatio": _closest_aspect_ratio(width, height),
    }
    if model.startswith("gemini-3"):
        image_config["imageSize"] = _image_size_for(width, height, model)

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": image_config,
        },
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload, headers=headers)
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
