"""Brand visual presets for image generation prompt injection."""

from __future__ import annotations

PRESETS: dict[str, str] = {
    "casey.berlin": (
        "Style: European editorial photography. Bureau Cool aesthetic. "
        "Mood: Contemplative, warm, considered. Monocle meets Die Zeit. "
        "Palette: Dark parchment, warm neutrals, ink tones, aged paper textures. "
        "Light: Natural, warm. Morning or late afternoon. Soft shadows. "
        "Composition: Considered negative space. Off-center subjects. Overhead or 3/4 angle. "
        "Elements: Coffee, notebooks, pencils, Berlin architecture, walks, analog textures. "
        "Typography (if text): Serif, elegant, understated. "
        "Never: Stock photo feel, corporate blue, gradient backgrounds, forced smiles."
    ),
    "cdit-works.de": (
        "Style: Scandinavian craft meets developer precision. Bakken & Bæck aesthetic. "
        "Mood: Clean, competent, trustworthy. Shows the work. "
        "Palette: Neutral grays (#f5f5f5 to #1a1a1a), one accent color, clear borders. "
        "Light: Even, studio-like. Clean and balanced. "
        "Composition: Grid-aligned, systematic. Tools and workspaces. "
        "Elements: Code editors, terminal windows, clean desks, architectural diagrams. "
        "Typography (if text): Geist Sans / monospace. System font feel. "
        "Never: Clip art, busy infographics, tech-bro energy, startup culture visuals."
    ),
    "storykeep": (
        "Style: Museum-grade, curatorial. White space as design element. "
        "Mood: Reverent, considered, archival. High culture. "
        "Palette: Neutral frames (white, light gray), rich content colors pulled from exhibition. "
        "Light: Gallery lighting — even, respectful of the subject. "
        "Composition: Gallery perspective. Frame within frame. Exhibition context. "
        "Elements: Art objects, archival materials, gallery interiors, family photographs. "
        "Typography (if text): Elegant serif for titles, clean sans for labels. "
        "Never: Playful, casual, social-media-first, filters, heavy post-processing."
    ),
    "nah": (
        "Style: Lo-fi, intentional imperfection. Anti-surveillance, anti-attention-economy. "
        "Mood: Warm, community-focused, handmade feel. "
        "Palette: Muted earth tones, organic colors. No neon, no gradients. "
        "Light: Natural, ambient. Imperfect. "
        "Composition: Informal, candid. Real spaces, real people. "
        "Elements: Local places, community gatherings, handwritten notes, analog media. "
        "Typography (if text): Rounded, approachable. Feels handmade, not designed. "
        "Never: Glossy, corporate, anything resembling Big Tech marketing."
    ),
    "yorizon": (
        "Style: Enterprise SaaS professional. Clean, polished, corporate-appropriate. "
        "Mood: Collaborative, forward-thinking, team-oriented. "
        "Palette: Yorizon brand colors ONLY. No CDiT branding, no personal aesthetic. "
        "Light: Professional, well-lit. Office/product context. "
        "Composition: Standard enterprise — team collaboration, product UI, professional settings. "
        "Elements: Product screenshots, team photos, enterprise workspace. "
        "Typography (if text): Corporate-clean sans-serif. "
        "Never: Personal brand aesthetic, CDiT colors/logo, consulting language, 'I' perspective."
    ),
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


def get_preset(context: str) -> str:
    """Get the brand visual preset for a context. Falls back to cdit-works.de."""
    # Normalize context name
    normalized = context.lower().strip().lstrip("@")
    for key, preset in PRESETS.items():
        if normalized in key or key in normalized:
            return preset
    return PRESETS["cdit-works.de"]


def get_dimensions(platform: str) -> tuple[int, int]:
    """Get dimensions for a platform. Falls back to 1200x1200."""
    normalized = platform.lower().strip().replace(" ", "-")
    return PLATFORM_SIZES.get(normalized, (1200, 1200))


def route_model(
    context: str | None = None,
    platform: str | None = None,
    model_hint: str | None = None,
) -> str:
    """Route to the optimal image generation model.

    Priority:
    1. Explicit model_hint overrides everything
    2. Vector/icon/illustration → Recraft (unique capability)
    3. Everything else → FLUX.2 Pro (best quality, regardless of cost)
    """
    if model_hint:
        return model_hint

    # Recraft for vectors/icons — unique capability FLUX can't do
    if platform:
        p = platform.lower()
        if any(kw in p for kw in ("icon", "svg", "vector", "logo", "illustration")):
            return "recraft"

    # FLUX for everything else — best quality, always
    return "flux"
