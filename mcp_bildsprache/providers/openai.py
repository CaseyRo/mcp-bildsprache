"""OpenAI GPT Image 2 provider (CDI-1014 §4).

Uses httpx directly (matching the pattern of the other providers) rather
than the `openai` SDK to keep the dependency footprint small. The Images
API is stable enough that a raw POST is fine.

Endpoint: POST https://api.openai.com/v1/images/generations

Defaults:
- model:          gpt-image-2 (configurable via OPENAI_IMAGE_MODEL)
- quality:        medium
- output_format:  webp
- compression:    90 (only applied for jpeg/webp)
- background:     opaque (transparent is NOT supported on gpt-image-2)
- moderation:     auto

Guardrails:
- Strip input_fidelity from any caller kwargs (API rejects it for gpt-image-2).
- Reject background="transparent" explicitly with a clear error.
- Validate size against OpenAI constraints before dispatch; snap non-
  compliant sizes to the nearest legal size. The existing post-processing
  pipeline trims to the caller's exact target size.
- Exponential backoff on 429 (3 retries, ~30s total budget); after budget
  exhaustion raise a structured RuntimeError the MCP tool surfaces as
  PROVIDER_RATE_LIMITED.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import math
import random
from typing import Any

import httpx

from mcp_bildsprache.config import settings
from mcp_bildsprache.types import ProviderResult

logger = logging.getLogger(__name__)

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"

# OpenAI size constraints for gpt-image-2 (from the docs as of 2026-04-24):
#   - max edge <= 3840 px
#   - both edges multiples of 16
#   - aspect ratio <= 3:1
#   - total pixels in [655_360, 8_294_400]
_MAX_EDGE = 3840
_SNAP = 16
_MAX_RATIO = 3.0
_MIN_PIXELS = 655_360
_MAX_PIXELS = 8_294_400

# Known-good quality presets. We default to medium and only promote to high
# on explicit caller opt-in. Low is exposed via a draft=true flag upstream.
_QUALITIES = ("low", "medium", "high", "auto")


class OpenAISizeError(ValueError):
    """Raised when the requested size cannot be made OpenAI-compliant."""


class OpenAIRateLimited(RuntimeError):
    """Raised after exponential-backoff budget is exhausted on 429."""


def _validate_and_snap_size(width: int, height: int) -> tuple[int, int]:
    """Return the nearest OpenAI-compliant size for a caller-requested WxH.

    Raises OpenAISizeError if the input is fundamentally out of bounds
    (ratio >3:1 or pixels outside the supported range).
    """
    if width <= 0 or height <= 0:
        raise OpenAISizeError(f"invalid dimensions {width}x{height}")

    ratio = max(width, height) / min(width, height)
    if ratio > _MAX_RATIO:
        raise OpenAISizeError(
            f"aspect ratio {ratio:.2f}:1 exceeds OpenAI max of 3:1 for {width}x{height}"
        )

    # Cap edges FIRST (proportionally) so an over-sized request like
    # 5000x1700 scales down to fit 3840 max edge before we check pixels.
    if max(width, height) > _MAX_EDGE:
        scale = _MAX_EDGE / max(width, height)
        width = int(width * scale)
        height = int(height * scale)

    pixels = width * height
    if pixels > _MAX_PIXELS:
        raise OpenAISizeError(
            f"pixel count {pixels} exceeds OpenAI max of {_MAX_PIXELS} for {width}x{height}"
        )
    if pixels < _MIN_PIXELS:
        # Caller asked for smaller than OpenAI supports — scale up to the
        # minimum while preserving aspect ratio. Post-crop trims it back.
        scale = math.sqrt(_MIN_PIXELS / pixels)
        width = max(width, int(width * scale))
        height = max(height, int(height * scale))

    # Snap to nearest multiple of 16, capped at max edge.
    width = min(_MAX_EDGE, max(_SNAP, round(width / _SNAP) * _SNAP))
    height = min(_MAX_EDGE, max(_SNAP, round(height / _SNAP) * _SNAP))

    # Final sanity — after snapping pixel count could dip below min.
    if width * height < _MIN_PIXELS:
        scale = math.sqrt(_MIN_PIXELS / (width * height))
        width = min(_MAX_EDGE, round(width * scale / _SNAP) * _SNAP)
        height = min(_MAX_EDGE, round(height * scale / _SNAP) * _SNAP)

    return width, height


def _strip_unsupported_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop parameters gpt-image-2 does not accept."""
    cleaned = dict(kwargs)
    if cleaned.pop("input_fidelity", None) is not None:
        logger.debug("openai: stripped input_fidelity (not supported on gpt-image-2)")
    return cleaned


async def generate_openai(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    *,
    reference_images: list[bytes] | None = None,
    quality: str = "medium",
    output_format: str = "webp",
    output_compression: int = 90,
    background: str = "opaque",
    moderation: str = "auto",
    draft: bool = False,
    stream: bool = False,
    **kwargs: Any,
) -> ProviderResult:
    """Generate an image using OpenAI gpt-image-2.

    Args:
        prompt: Text prompt.
        width / height: Caller-requested pixel dimensions. Snapped to the
            nearest OpenAI-compliant size for the API call; the existing
            pipeline trims to the exact requested size post-download.
        reference_images: Not supported in v1 (OpenAI's edit endpoint is
            tracked as a follow-up change). Silently ignored with a log.
        quality: "low" | "medium" | "high" | "auto". Default "medium".
        output_format: "webp" (default), "png", or "jpeg".
        output_compression: 0-100. Only applied for jpeg/webp.
        background: "opaque" | "auto". "transparent" is NOT supported on
            gpt-image-2 and is rejected explicitly.
        moderation: "auto" | "low". Default "auto".
        draft: If True, routes to the cheap tier (gpt-image-1-mini).
        stream: Not supported in v1 — rejected with a clear error.

    Returns:
        ProviderResult with raw bytes, usage block, revised_prompt (when
        OpenAI returns one), and model_version pinned to the model id used.

    Raises:
        ValueError: OPENAI_API_KEY not set, invalid params, transparent
            background, streaming requested.
        OpenAISizeError: size inputs fundamentally out of bounds.
        OpenAIRateLimited: 429 after retry budget.
        httpx.HTTPStatusError: other HTTP errors propagated to caller.
    """
    api_key = settings.openai_api_key.get_secret_value()
    if not api_key:
        raise ValueError("OPENAI_API_KEY not configured")

    if stream:
        raise ValueError("openai: streaming is not enabled in v1")

    if background == "transparent":
        raise ValueError(
            "openai: gpt-image-2 does not support background='transparent'. "
            "Use 'opaque' or 'auto'."
        )
    if background not in ("opaque", "auto"):
        raise ValueError(f"openai: unsupported background '{background}'")

    if quality not in _QUALITIES:
        raise ValueError(f"openai: unsupported quality '{quality}' (allowed: {_QUALITIES})")

    if reference_images:
        logger.info(
            "openai: dropping %d reference image(s) — edit endpoint not wired in v1",
            len(reference_images),
        )

    # Strip params gpt-image-2 rejects.
    _strip_unsupported_kwargs(kwargs)

    # Resolve model based on draft flag; allow caller override via kwargs.
    model = kwargs.pop(
        "model",
        settings.openai_image_model_draft if draft else settings.openai_image_model,
    )

    snapped_w, snapped_h = _validate_and_snap_size(width, height)
    size = f"{snapped_w}x{snapped_h}"

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "n": 1,
        "output_format": output_format,
        "background": background,
        "moderation": moderation,
    }
    # output_compression only valid for jpeg/webp
    if output_format in ("jpeg", "webp"):
        payload["output_compression"] = int(output_compression)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await _post_with_backoff(client, payload, headers)

    data = response.json()
    if not data.get("data"):
        raise ValueError(f"openai: empty response data: {data}")

    entry = data["data"][0]
    b64 = entry.get("b64_json")
    if not b64:
        raise ValueError(f"openai: response missing b64_json: {entry}")
    image_bytes = base64.b64decode(b64)

    mime = {
        "webp": "image/webp",
        "png": "image/png",
        "jpeg": "image/jpeg",
    }.get(output_format, "image/webp")

    usage = data.get("usage") or {}
    revised_prompt = entry.get("revised_prompt")

    # Compute a legacy cost string for backward-compat. The authoritative
    # cost lives in ai_attribution; this string is what pre-attribution
    # callers still read.
    cost_estimate = _legacy_cost_string(model, usage)

    return ProviderResult(
        image_data=image_bytes,
        mime_type=mime,
        model=model,
        cost_estimate=cost_estimate,
        usage=usage,
        revised_prompt=revised_prompt,
        model_version=model,
        provenance_flags={"synthid": False, "c2pa": False},
    )


async def _post_with_backoff(
    client: httpx.AsyncClient, payload: dict[str, Any], headers: dict[str, str]
) -> httpx.Response:
    """POST with exponential backoff on 429. Budget: 3 retries, ~30s total."""
    delays = (1.0, 4.0, 10.0)  # backoff schedule; max_total ~= 15s with jitter
    for attempt, delay in enumerate(delays + (None,)):  # type: ignore[operator]
        response = await client.post(OPENAI_IMAGES_URL, json=payload, headers=headers)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        if delay is None:
            raise OpenAIRateLimited(
                "openai: 429 after retry budget exhausted (3 retries, ~15s)"
            )
        # Jitter: +/- 25% so parallel clients don't all retry in sync.
        jittered = delay * (0.75 + 0.5 * random.random())
        logger.warning(
            "openai: 429 on attempt %d — sleeping %.1fs before retry",
            attempt + 1,
            jittered,
        )
        await asyncio.sleep(jittered)
    # Unreachable but keeps type checkers happy.
    raise OpenAIRateLimited("openai: unreachable backoff exit")


def _legacy_cost_string(model: str, usage: dict[str, Any]) -> str:
    """Approximate cost string from usage counts (backward-compat display only).

    The authoritative figure comes from attribution.compute_cost via the
    shared cost table. This is purely for the legacy `cost_estimate` field
    in case the attribution path is disabled.
    """
    # Published rates per 1M tokens (2026-04-24):
    rates = {
        "gpt-image-2": (8.0, 30.0),
        "gpt-image-1.5": (8.0, 32.0),
        "gpt-image-1-mini": (2.5, 8.0),
    }
    rate_in, rate_out = rates.get(model, (8.0, 30.0))
    in_tokens = usage.get("input_tokens") or 0
    out_tokens = usage.get("output_tokens") or 0
    usd = in_tokens * rate_in / 1_000_000 + out_tokens * rate_out / 1_000_000
    return f"${usd:.4f}"
