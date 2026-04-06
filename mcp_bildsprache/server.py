"""FastMCP server for brand-aware image generation."""

from __future__ import annotations

import base64
import logging
import os
import uuid

import httpx
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
    if not getattr(settings, 'keycloak_client_secret', ''):
        logger.warning("KEYCLOAK_CLIENT_SECRET is empty — OAuth/OIDC auth disabled")
        return None
    api_key = settings.ensure_api_key()
    return create_auth(
        api_key=api_key,
        base_url=settings.base_url,
        keycloak_issuer=settings.keycloak_issuer,
        keycloak_audience=settings.keycloak_audience,
    )


mcp = FastMCP("mcp-bildsprache", auth=_build_auth())


def _setup_hosting() -> None:
    """Mount static file serving for /data/images when ENABLE_HOSTING=true."""
    if not settings.enable_hosting:
        return

    images_dir = settings.images_dir
    os.makedirs(images_dir, exist_ok=True)
    logger.warning("Static file serving enabled at %s", images_dir)

    try:
        from starlette.routing import Mount
        from starlette.staticfiles import StaticFiles
        mcp._additional_http_routes.append(
            Mount("/images", app=StaticFiles(directory=images_dir, check_dir=False), name="images")
        )
    except Exception as exc:
        logger.warning("Could not mount static files: %s", exc)


async def _save_image_and_get_url(result: dict) -> str | None:
    """Save generated image to disk and return its hosted URL. Returns None if save fails."""
    images_dir = settings.images_dir
    os.makedirs(images_dir, exist_ok=True)

    image_id = str(uuid.uuid4())

    if "image_base64" in result:
        # Gemini: inline base64 data
        mime = result.get("mime_type", "image/png")
        ext = mime.split("/")[-1].replace("jpeg", "jpg")
        filename = f"{image_id}.{ext}"
        filepath = os.path.join(images_dir, filename)
        image_bytes = base64.b64decode(result["image_base64"])
        with open(filepath, "wb") as f:
            f.write(image_bytes)
        return f"{settings.base_url}/images/{filename}"

    if "image_url" in result:
        # BFL / Recraft: remote URL — download and save
        source_url = result["image_url"]
        ext = "jpg"
        if ".png" in source_url:
            ext = "png"
        filename = f"{image_id}.{ext}"
        filepath = os.path.join(images_dir, filename)
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(source_url)
                resp.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(resp.content)
            return f"{settings.base_url}/images/{filename}"
        except Exception as exc:
            logger.warning("Failed to download and save image from %s: %s", source_url, exc)
            return None

    return None


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

    if settings.enable_hosting:
        hosted_url = await _save_image_and_get_url(result)
        if hosted_url:
            result["hosted_url"] = hosted_url

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
    _setup_hosting()
    if settings.transport == "http":
        mcp.run(transport="http", host=settings.host, port=settings.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
