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

# Active brands (May 2026 brand collapse): ``casey`` and ``yorizon``.
# Former ``casey-berlin`` and ``cdit-works`` merged into ``casey`` (with
# personal/professional registers handled at the preset layer);
# ``storykeep`` and ``nah`` removed.
CANONICAL_BRANDS: tuple[str, ...] = (
    "casey",
    "yorizon",
)

# Maps every legacy/foreign slug variant to the active brand. The collapsed
# brands all funnel into ``casey``; ``yorizon`` keeps its identity.
_ALIASES: dict[str, str] = {
    # Casey-side legacy forms.
    "@casey": "casey",
    "@casey.berlin": "casey",
    "casey.berlin": "casey",
    "casey-berlin": "casey",
    "casey_berlin": "casey",
    # Former cdit-works (now the professional register on casey).
    "@cdit": "casey",
    "cdit": "casey",
    "@cdit-works": "casey",
    "@cdit.works": "casey",
    "cdit-works": "casey",
    "cdit-works.de": "casey",
    "cdit.works": "casey",
    "cdit_works": "casey",
    # Removed brands (now under casey/professional by default).
    "@storykeep": "casey",
    "storykeep": "casey",
    "@nah": "casey",
    "nah": "casey",
    # Yorizon stays separate.
    "@yorizon": "yorizon",
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
