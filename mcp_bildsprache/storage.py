"""Filesystem storage backend for generated images."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from mcp_bildsprache.config import settings
from mcp_bildsprache.slugs import make_collision_suffix, make_slug

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Raised when image storage fails after retries."""


def store_image(
    image_data: bytes,
    prompt: str,
    width: int,
    height: int,
    model: str,
    cost_estimate: str,
    brand_context: str | None = None,
    fallback_used: bool = False,
    original_model: str | None = None,
    attribution: dict | None = None,
) -> str:
    """Store a processed WebP image and return its hosted URL.

    Writes the image file and a JSON sidecar to the filesystem.
    Returns the full hosted URL (e.g. https://img.cdit-works.de/casey-berlin/slug-1200x630.webp).

    Raises StorageError if storage fails after one retry.
    """
    base_path = Path(settings.image_storage_path)
    brand_prefix, filename = make_slug(prompt, width, height, brand_context)

    dir_path = base_path / brand_prefix
    dir_path.mkdir(parents=True, exist_ok=True)

    file_path = dir_path / filename

    # Handle slug collision
    if file_path.exists():
        suffix = make_collision_suffix(image_data)
        stem = file_path.stem  # e.g. "morning-walk-1200x630"
        filename = f"{stem}-{suffix}.webp"
        file_path = dir_path / filename

    # Write image (retry once on failure)
    try:
        file_path.write_bytes(image_data)
    except OSError:
        logger.warning("First write attempt failed for %s — retrying", file_path)
        try:
            file_path.write_bytes(image_data)
        except OSError as e:
            raise StorageError(f"Failed to store image at {file_path}: {e}") from e

    # Write JSON sidecar. When an attribution payload is provided (CDI-1014),
    # it becomes the sidecar (schema-compliant ai_attribution v1) augmented
    # with file-specific fields. Otherwise fall back to the legacy shape.
    if attribution is not None:
        sidecar = dict(attribution)
        sidecar.setdefault("brand_context", brand_context)
        sidecar["file_size_bytes"] = len(image_data)
        sidecar["relative_path"] = f"{brand_prefix}/{filename}"
        sidecar["hosted_url"] = (
            f"{settings.image_domain.rstrip('/')}/{brand_prefix}/{filename}"
        )
        if fallback_used:
            sidecar["fallback_used"] = True
            sidecar["original_model"] = original_model
    else:
        sidecar = _build_sidecar(
            prompt=prompt,
            width=width,
            height=height,
            model=model,
            cost_estimate=cost_estimate,
            brand_context=brand_context,
            fallback_used=fallback_used,
            original_model=original_model,
            file_size=len(image_data),
            relative_path=f"{brand_prefix}/{filename}",
        )
    sidecar_path = file_path.with_suffix(".json")
    try:
        sidecar_path.write_text(json.dumps(sidecar, indent=2))
    except OSError as e:
        logger.warning("Failed to write sidecar %s: %s", sidecar_path, e)

    # Construct hosted URL
    domain = settings.image_domain.rstrip("/")
    return f"{domain}/{brand_prefix}/{filename}"


# Mime type → file extension mapping
_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def store_raw_image(
    image_data: bytes,
    mime_type: str,
    processed_file_path: str,
) -> str:
    """Store the unprocessed provider output alongside the processed image.

    Uses the same slug as the processed image but with a '-raw' suffix
    and the original file extension (e.g. .jpg, .png).

    Args:
        image_data: Raw bytes from the provider (no resize, no WebP conversion).
        mime_type: Original mime type from the provider.
        processed_file_path: The hosted URL of the processed image, used to derive the raw path.

    Returns the hosted URL for the raw image.
    """
    domain = settings.image_domain.rstrip("/")
    base_path = Path(settings.image_storage_path)

    # Derive raw filename from the processed URL
    # e.g. "https://img.cdit-works.de/casey-berlin/slug-1200x630.webp"
    #    → "casey-berlin/slug-1200x630.webp"
    relative = processed_file_path.removeprefix(domain).lstrip("/")
    processed_path = base_path / relative

    ext = _MIME_EXTENSIONS.get(mime_type, ".bin")
    raw_filename = f"{processed_path.stem}-raw{ext}"
    raw_path = processed_path.parent / raw_filename

    try:
        raw_path.write_bytes(image_data)
    except OSError as e:
        logger.warning("Failed to store raw image at %s: %s", raw_path, e)
        raise StorageError(f"Failed to store raw image: {e}") from e

    relative_raw = f"{processed_path.parent.name}/{raw_filename}"
    return f"{domain}/{relative_raw}"


def _build_sidecar(
    prompt: str,
    width: int,
    height: int,
    model: str,
    cost_estimate: str,
    brand_context: str | None,
    fallback_used: bool,
    original_model: str | None,
    file_size: int,
    relative_path: str,
) -> dict:
    """Build the JSON sidecar metadata record."""
    domain = settings.image_domain.rstrip("/")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt,
        "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(),
        "model": model,
        "brand_context": brand_context,
        "dimensions": f"{width}x{height}",
        "cost_estimate": cost_estimate,
        "file_size_bytes": file_size,
        "hosted_url": f"{domain}/{relative_path}",
        "fallback_used": fallback_used,
        "original_model": original_model,
    }
