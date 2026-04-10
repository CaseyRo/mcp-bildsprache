"""Image processing pipeline: resize, crop, convert, and inject metadata."""

from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import datetime, timezone

from PIL import Image, ImageOps

from mcp_bildsprache.types import ProviderResult

logger = logging.getLogger(__name__)


def process_image(
    provider_result: ProviderResult,
    target_width: int,
    target_height: int,
    prompt: str,
    brand_context: str | None = None,
    webp_quality: int = 90,
) -> bytes:
    """Process a provider result into a final WebP image.

    Pipeline: open → resize/crop to exact dimensions → convert to WebP → inject EXIF metadata.

    Returns WebP image bytes ready for storage.
    """
    img = Image.open(io.BytesIO(provider_result.image_data))

    # Convert to RGB if necessary (e.g. RGBA PNGs, palette mode)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    # Resize and crop to exact target dimensions (center crop)
    img = ImageOps.fit(img, (target_width, target_height), method=Image.LANCZOS)

    # Build EXIF metadata
    exif_bytes = _build_exif(
        prompt=prompt,
        model=provider_result.model,
        brand_context=brand_context,
    )

    # Convert to WebP
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=webp_quality, exif=exif_bytes)
    return buf.getvalue()


def _build_exif(
    prompt: str,
    model: str,
    brand_context: str | None,
) -> bytes:
    """Build EXIF metadata bytes for AI provenance.

    Embeds creator tool, rights, and a JSON user comment with prompt hash
    (never the full prompt) and generation metadata.
    """
    try:
        import piexif

        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        user_comment = json.dumps({
            "prompt_hash": prompt_hash,
            "model": model,
            "brand_context": brand_context,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "Bildsprache AI",
        })

        # piexif UserComment requires ASCII prefix
        user_comment_bytes = b"ASCII\x00\x00\x00" + user_comment.encode("ascii", errors="replace")

        exif_dict = {
            "0th": {
                piexif.ImageIFD.Software: "Bildsprache AI",
                piexif.ImageIFD.Copyright: "AI-generated content",
            },
            "Exif": {
                piexif.ExifIFD.UserComment: user_comment_bytes,
            },
        }
        return piexif.dump(exif_dict)

    except ImportError:
        logger.warning("piexif not installed — skipping EXIF metadata injection")
        return b""
    except Exception as e:
        logger.warning("EXIF injection failed: %s — continuing without metadata", e)
        return b""
