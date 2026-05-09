"""Brand visual presets for image generation prompt injection.

May 2026 brand collapse: ``casey-berlin`` and ``cdit-works.de`` merged into
a single ``casey`` brand carrying one shared visual preset across two
registers (``personal``, ``professional``). ``storykeep`` and ``nah``
removed. ``yorizon`` stays fully isolated (no shared palette tokens).

Per the same change, FLUX (BFL) and Recraft are disabled at the
dispatcher layer (``route_model`` raises ``ProviderTemporarilyDisabled``
on FLUX/Recraft hints). OpenAI ``gpt-image-2`` is the active raster
default; Gemini Nano Banana Pro is reserved for the diagram path
(``generate_diagram``).
"""

from __future__ import annotations

from typing import Literal

from mcp_bildsprache.types import ProviderTemporarilyDisabled

# ---------------------------------------------------------------------------
# Casey botanical palette (locked, May 2026 brand-decisions doc)
# ---------------------------------------------------------------------------

CASEY_PALETTE: dict[str, dict[str, str]] = {
    "paper_bone": {
        "hex": "#F4EFE3",
        "oklch": "oklch(0.97 0.012 80)",
        "role": "background, ~70% of every surface",
    },
    "forest_moss": {
        "hex": "#2C4A38",
        "oklch": "oklch(0.32 0.06 155)",
        "role": "wordmarks, key links, drenched grounds",
    },
    "pine_ink": {
        "hex": "#1F2E26",
        "oklch": "oklch(0.30 0.02 145)",
        "role": "body text",
    },
    "weathered_ochre": {
        "hex": "#B8884A",
        "oklch": "oklch(0.68 0.10 80)",
        "role": "accent ≤5%: links, marks, hairlines, drift words",
    },
    "soft_moss": {
        "hex": "#C7CFB8",
        "oklch": "oklch(0.84 0.02 130)",
        "role": "hairlines and rules only",
    },
}


def _casey_palette_clause() -> str:
    """Render the locked palette into a prompt clause for any casey image."""
    return (
        "Palette (botanical, locked May 2026): paper bone #F4EFE3 background "
        "(~70% of surface), forest moss #2C4A38 for primary form (wordmarks, "
        "key links, drenched grounds), pine ink #1F2E26 for body text, "
        "weathered ochre #B8884A as accent (≤5% — links, marks, hairlines, "
        "drift words), soft moss #C7CFB8 for hairlines and rules only."
    )


_CASEY_TYPOGRAPHY_CLAUSE: str = (
    "Typography (when in-image text appears): Vollkorn-style serif, "
    "italic + roman, weights 400–900. Hierarchy via weight + size, never "
    "via optical-size axis. NO all-caps anywhere — use weight or italic "
    "for emphasis."
)

_CASEY_ANTI_ANCHORS_CLAUSE: str = (
    "Avoid: chrome, lens flare, neon, gradient mesh, generic AI aesthetic. "
    "Avoid: stock photo feel, corporate blue, tech-bro energy. "
    "No proximity to: Musk personal brand, OpenAI marketing aesthetic, "
    "tech-launch glamour."
)

_CASEY_BASE: str = (
    f"Brand: casey (one voice, two registers). "
    f"{_casey_palette_clause()} "
    f"{_CASEY_TYPOGRAPHY_CLAUSE} "
    f"{_CASEY_ANTI_ANCHORS_CLAUSE}"
)

_CASEY_REGISTER_PERSONAL: str = (
    "Register: personal (recognition surface — who Casey is). "
    "Mood: warmer, kitchen-table, late-afternoon light, intimate framing. "
    "Composition: hands and objects, walks, slow correspondence. "
    "Anchor refs: Patagonia (rootedness, place), Apple (restraint as "
    "confidence). Lower contrast, more bone, more sensory texture."
)

_CASEY_REGISTER_PROFESSIONAL: str = (
    "Register: professional (verification surface — what Casey ships). "
    "Mood: crisper, schematic clarity, neutral light, restrained composition. "
    "Composition: workshop frame, tools, what-I-shipped detail. "
    "Anchor refs: Anthropic (thoughtfulness), Apple (careful innovation), "
    "Patagonia (values-beyond-profit). Higher contrast, more white space, "
    "tool-and-object focus."
)

# Compositional rule applied when the casey identity pack resolves to a
# non-empty list of reference slots (i.e. a person/dog is plausibly in
# the frame). Gated by server.py — not embedded in the PRESETS dict so
# that person-excluding prompts (icon, abstract pattern, etc.) stay clean.
CASEY_COMPOSITION_CLAUSE: str = (
    "Composition: when a person appears, they are embedded in the scene "
    "doing something, never face-to-camera, never centered as the sole "
    "focal point. If multiple people are present, the subject is one of "
    "them — not the lead."
)

# ---------------------------------------------------------------------------
# Active presets
# ---------------------------------------------------------------------------

PRESETS: dict[str, str] = {
    "casey": _CASEY_BASE,
    "yorizon": (
        "Style: Enterprise SaaS professional. Clean, polished, "
        "corporate-appropriate. "
        "Mood: Collaborative, forward-thinking, team-oriented. "
        "Palette: Yorizon brand colours ONLY. NO casey botanical tokens "
        "(no paper bone, no forest moss, no weathered ochre). NO "
        "casey-personal aesthetic. "
        "Light: Professional, well-lit. Office/product context. "
        "Composition: Standard enterprise — team collaboration, product "
        "UI, professional settings. "
        "Elements: Product screenshots, team photos, enterprise workspace. "
        "Typography: Corporate-clean sans-serif. "
        "Never: Personal brand aesthetic, casey palette tokens, "
        "consulting language, 'I' perspective, Vollkorn typography."
    ),
}

# Per-register overlays for the casey brand.
CASEY_REGISTER_OVERLAYS: dict[str, str] = {
    "personal": _CASEY_REGISTER_PERSONAL,
    "professional": _CASEY_REGISTER_PROFESSIONAL,
}

# Internal mapping from canonical brand → preset key for fallback purposes.
_REMOVED_PRESETS: dict[str, str] = {
    "casey-berlin": "casey",
    "cdit-works": "casey",
    "cdit-works.de": "casey",
    "casey.berlin": "casey",
    "storykeep": "casey",
    "nah": "casey",
}

# Platform sizing presets (width x height)
PLATFORM_SIZES: dict[str, tuple[int, int]] = {
    "linkedin-post": (1200, 1200),
    "linkedin-article": (1200, 628),
    "linkedin-carousel": (1080, 1350),
    "instagram-feed": (1080, 1080),
    "instagram-story": (1080, 1920),
    "blog-hero": (1600, 900),
    "og-image": (1200, 630),
    "proposal-cover": (2480, 3508),
    "icon": (512, 512),
    "email-header": (600, 200),
}


def get_preset(
    context: str,
    register: Literal["personal", "professional"] | None = None,
) -> str:
    """Get the brand visual preset for a context, optionally with a register overlay.

    Active brands: ``casey``, ``yorizon``. Legacy keys (``casey-berlin``,
    ``cdit-works``, ``storykeep``, ``nah``, ``casey.berlin``,
    ``cdit-works.de``) resolve to the ``casey`` preset for backward compat
    on internal callers — but new code should use ``casey`` directly with
    the appropriate register.

    For ``brand="casey"`` with a register, the matching overlay
    (``CASEY_REGISTER_OVERLAYS[register]``) is appended to the base
    preset. Yorizon ignores the register argument.
    """
    from mcp_bildsprache.brands import normalize_brand

    canonical = normalize_brand(context) or context

    # Direct hit on canonical → matching internal PRESETS key.
    if canonical in PRESETS:
        base = PRESETS[canonical]
        if canonical == "casey" and register and register in CASEY_REGISTER_OVERLAYS:
            return f"{base} {CASEY_REGISTER_OVERLAYS[register]}"
        return base

    # Legacy keys → casey preset (with register if provided).
    if canonical in _REMOVED_PRESETS:
        target = _REMOVED_PRESETS[canonical]
        base = PRESETS.get(target, PRESETS["casey"])
        if target == "casey" and register and register in CASEY_REGISTER_OVERLAYS:
            return f"{base} {CASEY_REGISTER_OVERLAYS[register]}"
        return base

    # Unknown context → casey with professional register (the safer default).
    base = PRESETS["casey"]
    if register and register in CASEY_REGISTER_OVERLAYS:
        return f"{base} {CASEY_REGISTER_OVERLAYS[register]}"
    return f"{base} {CASEY_REGISTER_OVERLAYS['professional']}"


def get_dimensions(platform: str) -> tuple[int, int]:
    """Get dimensions for a platform. Falls back to 1200x1200."""
    normalized = platform.lower().strip().replace(" ", "-")
    return PLATFORM_SIZES.get(normalized, (1200, 1200))


def route_model(
    context: str | None = None,
    platform: str | None = None,
    model_hint: str | None = None,
    has_references: bool = False,
    intent: Literal["raster", "diagram"] = "raster",
) -> str:
    """Route to the optimal image generation provider.

    Returns a provider key: "openai" or "gemini". Per the May 2026 brand
    collapse, FLUX (BFL) and Recraft are temporarily disabled — hinting at
    them raises ``ProviderTemporarilyDisabled``.

    Routing logic:
    1. Explicit model_hint:
       - "openai" / "gpt-image-*" → openai
       - "gemini" / "gemini-*" / "nano-banana*" → gemini
       - "flux" / "flux-*" / "bfl" → ProviderTemporarilyDisabled
       - "recraft" / "recraft*" → ProviderTemporarilyDisabled
    2. intent="diagram" → gemini (default for diagrams)
    3. intent="raster" (default) → openai

    NOTE: Reference images are routed to openai when intent="raster"
    (gpt-image-2 supports them natively via image[]=). When intent="diagram"
    references aren't applicable; if hinted alongside a diagram, the caller
    is expected to use generate_image instead.
    """
    if model_hint:
        h = model_hint.lower().strip()
        if h.startswith("openai") or h.startswith("gpt-image"):
            return "openai"
        if h.startswith("gemini") or h.startswith("nano-banana") or h.startswith("nano_banana"):
            return "gemini"
        if h.startswith("flux") or h.startswith("bfl"):
            replacement = "openai" if intent == "raster" else "gemini"
            raise ProviderTemporarilyDisabled(
                provider="FLUX",
                replacement=replacement,
            )
        if h.startswith("recraft"):
            replacement = "openai" if intent == "raster" else "gemini"
            raise ProviderTemporarilyDisabled(
                provider="Recraft",
                replacement=replacement,
            )
        raise ValueError(
            f"Unknown model: {model_hint}. Valid: openai, gpt-image-2, "
            "gemini, nano-banana-pro. Disabled: flux, recraft."
        )

    if intent == "diagram":
        return "gemini"

    # Default raster path → OpenAI gpt-image-2.
    return "openai"


# Active provider list (advertised via list_models).
ACTIVE_PROVIDERS: tuple[str, ...] = ("openai", "gemini")

# Disabled providers (still in-tree, not dispatched).
DISABLED_PROVIDERS: tuple[dict[str, str], ...] = (
    {
        "provider": "bfl",
        "reason": (
            "Disabled at dispatcher per the May 2026 brand-collapse change. "
            "Module remains in-tree at providers/bfl.py for re-enable."
        ),
    },
    {
        "provider": "recraft",
        "reason": (
            "Disabled at dispatcher per the May 2026 brand-collapse change. "
            "Module remains in-tree at providers/recraft.py for re-enable."
        ),
    },
)
