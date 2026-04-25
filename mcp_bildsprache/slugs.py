"""Slug generation for shareable image URLs."""

from __future__ import annotations

import hashlib

from slugify import slugify

# Brand context → URL prefix mapping.
#
# Iteration order matters: identity._brand_context_for_dir returns the
# FIRST matching entry's key as the pack-lookup name, so the legacy
# dot-separated form must come before the canonical bare slug to keep
# the loader emitting "@casey.berlin"-style keys (existing tests + any
# downstream callers depend on it).
#
# Canonical bare slugs (CDI-1041) are added at the end so direct lookups
# from `_resolve_brand_prefix` after normalize_brand still hit a value.
BRAND_PREFIXES = {
    # Legacy / internal forms (preserve order — _brand_context_for_dir consumer)
    "casey.berlin": "casey-berlin",
    "cdit-works.de": "cdit",
    "cdit": "cdit",
    "storykeep": "storykeep",
    "nah": "nah",
    "yorizon": "yorizon",
    # Canonical bare slugs (CDI-1041) — for direct lookup post-normalize
    "casey-berlin": "casey-berlin",
    "cdit-works": "cdit",
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
