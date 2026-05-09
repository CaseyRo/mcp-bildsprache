"""Slug generation for shareable image URLs."""

from __future__ import annotations

import hashlib

from slugify import slugify

# Brand context → URL prefix mapping.
#
# Active brands (May 2026 collapse): ``casey`` → ``casey/``, ``yorizon`` →
# ``yorizon/``. Historical prefixes (``casey-berlin/``, ``cdit/``,
# ``storykeep/``, ``nah/``) remain on the static mount so old URLs still
# resolve, but new generations land under the active prefixes.
#
# Iteration order matters: identity._brand_context_for_dir returns the
# FIRST matching entry's key as the pack-lookup name. Active forms are
# listed first so new lookups hit them; legacy forms remain for backward
# compat on directory → context resolution.
BRAND_PREFIXES = {
    # Active brands.
    "casey": "casey",
    "yorizon": "yorizon",
    # Legacy → active mappings (so old context strings still resolve).
    "casey.berlin": "casey",
    "casey-berlin": "casey",
    "cdit-works.de": "casey",
    "cdit-works": "casey",
    "cdit": "casey",
    "storykeep": "casey",
    "nah": "casey",
}

MAX_SLUG_LENGTH = 60


def make_slug(
    prompt: str,
    width: int,
    height: int,
    brand_context: str | None = None,
) -> tuple[str, str]:
    """Generate a brand-prefixed slug for an image URL.

    Returns (brand_prefix, filename) where:
    - brand_prefix: directory name (e.g. "casey-berlin", "gen")
    - filename: slug with dimensions (e.g. "morning-walk-kreuzberg-1200x630.webp")
    """
    brand_prefix = _resolve_brand_prefix(brand_context)
    prompt_slug = slugify(prompt, max_length=MAX_SLUG_LENGTH)
    if not prompt_slug:
        prompt_slug = "image"
    filename = f"{prompt_slug}-{width}x{height}.webp"
    return brand_prefix, filename


def make_collision_suffix(image_data: bytes) -> str:
    """Generate a short hash suffix for collision handling."""
    return hashlib.sha256(image_data).hexdigest()[:4]


def _resolve_brand_prefix(context: str | None) -> str:
    """Map a brand context to a URL-safe directory prefix.

    Accepts canonical bare slugs and legacy variants. Falls back to "gen"
    for unknown contexts.
    """
    if not context:
        return "gen"

    from mcp_bildsprache.brands import normalize_brand

    canonical = normalize_brand(context) or context
    if canonical in BRAND_PREFIXES:
        return BRAND_PREFIXES[canonical]

    # Legacy fuzzy-match path for any variant that slips through normalisation.
    normalized = canonical.lower().strip().lstrip("@")
    for key, prefix in BRAND_PREFIXES.items():
        if normalized in key or key in normalized:
            return prefix
    return "gen"
