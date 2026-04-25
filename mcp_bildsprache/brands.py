"""Brand-slug normalisation (CDI-1041 cross-skill alignment).

The fleet has historically used three different conventions for the same
brand:
- mcp-bildsprache: ``@cdit``, ``@casey.berlin`` (this repo)
- mcp-klartext:    ``cdit-works`` mostly, ``@cdit-works`` in some docs
- mcp-writings:    ``cdit-works`` (canonical, matches syndicate enum)

Canonical form across the fleet is the **bare hyphenated slug** —
``casey-berlin``, ``cdit-works``, ``storykeep``, ``nah``, ``yorizon``.
Everything else aliases to one of those at the lookup boundary.

This module is the single source of truth for that mapping inside
mcp-bildsprache. Other lookups (``presets.get_preset``,
``slugs._resolve_brand_prefix``, ``identity.get_pack_for_context``)
should call ``normalize_brand`` before consulting their internal maps.
"""

from __future__ import annotations

CANONICAL_BRANDS: tuple[str, ...] = (
    "casey-berlin",
    "cdit-works",
    "storykeep",
    "nah",
    "yorizon",
)

# Maps every legacy/foreign slug variant we've seen to the canonical form.
# Keep conservative — only forms we've actually observed in the wild.
_ALIASES: dict[str, str] = {
    # Bildsprache historical (@-prefixed, dot-separated, abbreviated)
    "@casey.berlin": "casey-berlin",
    "casey.berlin": "casey-berlin",
    "@cdit": "cdit-works",
    "cdit": "cdit-works",
    "@cdit-works": "cdit-works",
    "@cdit.works": "cdit-works",
    "cdit-works.de": "cdit-works",
    "cdit.works": "cdit-works",
    "@storykeep": "storykeep",
    "@nah": "nah",
    "@yorizon": "yorizon",
    # Underscored variants
    "casey_berlin": "casey-berlin",
    "cdit_works": "cdit-works",
}


def normalize_brand(brand: str | None) -> str | None:
    """Return the canonical bare-hyphenated slug for any known variant.

    None passes through (no brand context). Unknown strings pass through
    unchanged so existing fuzzy-matching fallbacks in callers still apply.
    """
    if not brand:
        return brand
    cleaned = brand.strip()
    if cleaned in CANONICAL_BRANDS:
        return cleaned
    # Direct alias hit (case-sensitive — the variants we accept are all
    # lowercase in practice).
    if cleaned in _ALIASES:
        return _ALIASES[cleaned]
    # Try lowercase + leading-@ stripped as a last alias-table pass.
    relaxed = cleaned.lower().lstrip("@")
    for needle in (cleaned, relaxed, f"@{relaxed}"):
        if needle in _ALIASES:
            return _ALIASES[needle]
    if relaxed in CANONICAL_BRANDS:
        return relaxed
    # Unknown — return original so the caller's existing fallback (e.g.
    # substring fuzzy match in get_preset) still has a chance.
    return cleaned


def is_known_brand(brand: str | None) -> bool:
    """True when ``brand`` resolves to a canonical slug."""
    if not brand:
        return False
    return normalize_brand(brand) in CANONICAL_BRANDS
