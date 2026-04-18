"""Black Forest Labs FLUX image generation provider."""

from __future__ import annotations

import asyncio
import base64
import io
import logging

import httpx
from PIL import Image

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
# When reference_images are present, we skip flux-2-max entirely (text-only
# model) and prefer reference-capable endpoints.
REFERENCE_FALLBACK_CHAIN = ["flux-kontext-pro", "flux-2-pro"]

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


def _collage(images: list[bytes]) -> bytes:
    """Combine N reference images into a single 1×N horizontal grid (PNG).

    Each source is resized to a common height while preserving its aspect
    ratio, then pasted left-to-right. If the resulting canvas would exceed
    the smallest FLUX reference-model max_mp (``flux-kontext-pro`` at
    1.0 MP), the whole collage is scaled down proportionally.
    """
    if not images:
        raise ValueError("_collage called with no images")
    if len(images) == 1:
        # Re-encode to PNG for consistency with the multi-image branch.
        with Image.open(io.BytesIO(images[0])) as img:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            return buf.getvalue()

    # Normalise each to a fixed height while preserving aspect ratio.
    target_h = 1024
    pieces: list[Image.Image] = []
    try:
        for blob in images:
            img = Image.open(io.BytesIO(blob)).convert("RGB")
            scale = target_h / img.height
            new_w = max(1, round(img.width * scale))
            pieces.append(img.resize((new_w, target_h), Image.LANCZOS))

        total_w = sum(p.width for p in pieces)

        # Downscale the whole collage if it exceeds the tightest FLUX
        # reference-model max_mp budget (flux-kontext-pro = 1.0 MP).
        max_mp = FLUX_MODELS["flux-kontext-pro"]["max_mp"]
        mp = (total_w * target_h) / 1_000_000
        if mp > max_mp:
            scale = (max_mp / mp) ** 0.5
            target_h = max(1, round(target_h * scale))
            pieces = [p.resize((max(1, round(p.width * scale)), target_h), Image.LANCZOS)
                      for p in pieces]
            total_w = sum(p.width for p in pieces)

        canvas = Image.new("RGB", (total_w, target_h), color=(0, 0, 0))
        x = 0
        for p in pieces:
            canvas.paste(p, (x, 0))
            x += p.width

        logger.info(
            "bfl_collage sources=%d width=%d height=%d",
            len(images),
            total_w,
            target_h,
        )

        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        for p in pieces:
            try:
                p.close()
            except Exception:
                pass


async def generate_bfl(
    prompt: str,
    width: int = 1600,
    height: int = 900,
    model: str | None = None,
    reference_images: list[bytes] | None = None,
) -> ProviderResult:
    """Generate an image using FLUX via BFL API.

    Uses FLUX.2 Max by default. Falls back through the model chain on failure.
    Downloads the image and returns a ProviderResult with raw bytes.

    When ``reference_images`` is non-empty, routes to a reference-capable
    FLUX model. The fallback chain becomes ``flux-kontext-pro → flux-2-pro``
    (the latter uses the ``image_prompt`` field). ``flux-2-max`` is never
    attempted because it is text-only. Multiple references are combined
    into a single-input collage before submission.
    """
    api_key = settings.bfl_api_key.get_secret_value()
    if not api_key:
        raise ValueError("BFL_API_KEY not configured")

    refs = reference_images or []

    # Prepare a single reference blob for single-input endpoints. For 1
    # reference we pass it through; for >1 we build a 1×N collage.
    reference_blob: bytes | None = None
    if refs:
        reference_blob = _collage(refs)

    if refs:
        # Reference-aware routing. If the caller pinned a specific model,
        # try it first (even flux-2-max would be surprising but we respect
        # explicit hints), then fall through the reference chain.
        if model and model in FLUX_MODELS and model not in REFERENCE_FALLBACK_CHAIN:
            models_to_try = [model] + [m for m in REFERENCE_FALLBACK_CHAIN if m != model]
        else:
            selected = model or REFERENCE_FALLBACK_CHAIN[0]
            models_to_try = [selected] + [m for m in REFERENCE_FALLBACK_CHAIN if m != selected]
    else:
        selected = model or DEFAULT_MODEL
        models_to_try = [selected] + [m for m in FALLBACK_CHAIN if m != selected]

    last_error = None
    for model_id in models_to_try:
        model_info = FLUX_MODELS.get(model_id)
        if not model_info:
            continue
        try:
            return await _generate_with_model(
                api_key, model_id, model_info, prompt, width, height, reference_blob
            )
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
    reference_blob: bytes | None = None,
) -> ProviderResult:
    """Generate with a specific FLUX model."""
    w, h = _snap_dimensions(width, height, model_info["snap"], model_info["max_mp"])

    headers = {
        "Content-Type": "application/json",
        "x-key": api_key,
    }

    payload: dict = {
        "prompt": prompt,
        "width": w,
        "height": h,
    }

    # Attach reference bytes using the field name each endpoint expects.
    if reference_blob is not None:
        encoded = base64.b64encode(reference_blob).decode("ascii")
        if model_id == "flux-kontext-pro":
            payload["input_image"] = encoded
        elif model_id == "flux-2-pro":
            payload["image_prompt"] = encoded
        # Other models (flux-2-max, flux-pro-1.1) do not accept references;
        # we silently omit the field rather than fail so that an explicit
        # model_hint still yields *some* output.

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
