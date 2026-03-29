"""FastMCP server for brand-aware image generation."""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from mcp_bildsprache.auth import create_auth
from mcp_bildsprache.config import settings
from mcp_bildsprache.presets import (
    PLATFORM_SIZES,
    PRESETS,
    get_dimensions,
    get_preset,
    route_model,
)
from mcp_bildsprache.providers.bfl import generate_bfl
from mcp_bildsprache.providers.gemini import generate_gemini
from mcp_bildsprache.providers.recraft import generate_recraft

logger = logging.getLogger(__name__)

PROVIDERS = {
    "gemini": generate_gemini,
    "flux": generate_bfl,
    "recraft": generate_recraft,
}

FALLBACKS = {
    "flux": "gemini",
    "gemini": "flux",
    "recraft": "gemini",
}


def _build_auth():
    """Build auth provider if running in HTTP mode."""
    if settings.transport != "http":
        return None
    api_key = settings.ensure_api_key()
    return create_auth(
        api_key=api_key,
        base_url=settings.base_url,
        keycloak_issuer=settings.keycloak_issuer,
        keycloak_audience=settings.keycloak_audience,
    )


mcp = FastMCP("mcp-bildsprache", auth=_build_auth())


@mcp.tool
async def generate_image(
    prompt: str,
    context: str | None = None,
    model: str | None = None,
    platform: str | None = None,
    dimensions: str | None = None,
    mood: str | None = None,
) -> dict:
    """Generate a brand-aware image.

    Args:
        prompt: Description of the image to generate.
        context: Brand context (@casey.berlin, @cdit, @storykeep, @nah, @yorizon).
                 If omitted, no brand preset is injected.
        model: Force a specific model (gemini, flux, recraft). Auto-routed if omitted.
        platform: Target platform (linkedin-post, blog-hero, etc.) for auto-sizing.
        dimensions: Explicit dimensions as 'WxH' (e.g., '1200x1200'). Overrides platform sizing.
        mood: Emotional register for the image (e.g., 'contemplative', 'energetic').
    """
    # Determine model
    selected_model = route_model(context=context, platform=platform, model_hint=model)

    # Determine dimensions
    if dimensions:
        w, h = (int(x) for x in dimensions.lower().split("x"))
    elif platform:
        w, h = get_dimensions(platform)
    else:
        w, h = 1200, 1200

    # Build enhanced prompt with brand preset
    parts = []
    if context:
        parts.append(get_preset(context))
    parts.append(prompt)
    if mood:
        parts.append(f"Mood/emotional register: {mood}")
    enhanced_prompt = "\n".join(parts)

    # Generate with fallback
    try:
        provider = PROVIDERS[selected_model]
        result = await provider(enhanced_prompt, w, h)
    except Exception as e:
        logger.warning("Provider %s failed: %s — trying fallback", selected_model, e)
        fallback_model = FALLBACKS.get(selected_model)
        if not fallback_model:
            raise
        provider = PROVIDERS[fallback_model]
        result = await provider(enhanced_prompt, w, h)
        result["fallback_used"] = True
        result["original_model"] = selected_model

    result["brand_context"] = context
    result["platform"] = platform
    result["dimensions"] = f"{w}x{h}"
    result["prompt_used"] = enhanced_prompt
    return result


@mcp.tool
async def generate_prompt(
    description: str,
    context: str | None = None,
    model: str | None = None,
    platform: str | None = None,
    mood: str | None = None,
) -> dict:
    """Generate an engineered image prompt without generating the image.

    Useful for previewing what will be sent to the model, or for manual generation.

    Args:
        description: What the image should show.
        context: Brand context for preset injection.
        model: Target model (affects prompt style).
        platform: Target platform (affects dimensions recommendation).
        mood: Emotional register.
    """
    selected_model = route_model(context=context, platform=platform, model_hint=model)

    parts = []
    if context:
        parts.append(get_preset(context))
    parts.append(description)
    if mood:
        parts.append(f"Mood/emotional register: {mood}")

    dimensions = get_dimensions(platform) if platform else (1200, 1200)

    return {
        "engineered_prompt": "\n".join(parts),
        "model": selected_model,
        "dimensions": f"{dimensions[0]}x{dimensions[1]}",
        "brand_context": context,
        "platform": platform,
    }


@mcp.tool
async def list_models() -> list[dict]:
    """List available image generation models and their capabilities."""
    available = []

    if settings.gemini_api_key.get_secret_value():
        available.append({
            "id": "gemini",
            "name": "Gemini (Nano Banana 2)",
            "model": "gemini-2.0-flash-exp",
            "best_for": "Social media graphics, text-on-image, quick iterations",
            "cost": "~$0.01/image",
            "status": "available",
        })

    if settings.bfl_api_key.get_secret_value():
        available.append({
            "id": "flux",
            "name": "FLUX.2 Pro (Black Forest Labs)",
            "model": "flux-pro-1.1",
            "best_for": "Editorial photography, hero images, cinematic quality",
            "cost": "~$0.04/image",
            "status": "available",
        })

    if settings.recraft_api_key.get_secret_value():
        available.append({
            "id": "recraft",
            "name": "Recraft V3",
            "model": "recraft-v3",
            "best_for": "Vectors, icons, SVG-style illustrations",
            "cost": "Free tier (50 daily credits)",
            "status": "available",
            "license": "free" if settings.recraft_tier == "free" else "pro",
        })

    return available


@mcp.tool
async def get_brand_presets(context: str | None = None) -> dict:
    """Get brand visual presets for image generation.

    Args:
        context: Specific brand context to retrieve. If omitted, returns all presets.
    """
    if context:
        return {
            "context": context,
            "preset": get_preset(context),
            "platforms": PLATFORM_SIZES,
        }
    return {
        "presets": PRESETS,
        "platforms": PLATFORM_SIZES,
    }


def main() -> None:
    """Entry point for the mcp-bildsprache server."""
    if settings.transport == "http":
        mcp.run(transport="http", host=settings.host, port=settings.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
