"""FastMCP server for brand-aware image generation."""

from __future__ import annotations

import logging
from typing import Literal

from fastmcp import FastMCP
from mcp.types import Icon

from pathlib import Path

from mcp_bildsprache.auth import BearerTokenVerifier, create_auth
from mcp_bildsprache.config import settings
from mcp_bildsprache.identity import (
    get_loaded_packs,
    get_pack_for_context,
    load_identity_packs,
    resolve_identity_for_call,
    set_loaded_packs,
)
from mcp_bildsprache.pipeline import process_image
from mcp_bildsprache.presets import (
    CASEY_COMPOSITION_CLAUSE,
    PLATFORM_SIZES,
    PRESETS,
    get_dimensions,
    get_preset,
    route_model,
)
from mcp_bildsprache.providers.bfl import generate_bfl
from mcp_bildsprache.providers.gemini import generate_gemini
from mcp_bildsprache.providers.recraft import generate_recraft
from mcp_bildsprache.storage import StorageError, store_image, store_raw_image
from mcp_bildsprache.types import IdentityPack, ProviderResult

logger = logging.getLogger(__name__)

Model = Literal[
    "gemini", "flux", "flux-2-max", "flux-2-pro",
    "flux-kontext-pro", "flux-pro-1.1", "recraft",
]
BrandContext = Literal["@casey.berlin", "@cdit", "@storykeep", "@nah", "@yorizon"]
Platform = Literal[
    "linkedin-post",
    "linkedin-article",
    "linkedin-carousel",
    "instagram-feed",
    "instagram-story",
    "blog-hero",
    "og-image",
    "proposal-cover",
    "icon",
    "email-header",
]

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

# When references are present we must not fall back to a text-only path.
# Both flux and gemini are reference-capable; recraft is not and should
# never be picked in this branch (and `route_model(has_references=True)`
# prevents it from being auto-selected in the first place).
REFERENCE_FALLBACKS = {
    "flux": "gemini",
    "gemini": "flux",
    "recraft": "flux",
}


# Module-level cache: reference image bytes are read from disk once per
# process and then reused for every call that resolves to the same file.
# Keyed by absolute Path; populated lazily on first need.
_REFERENCE_BYTES_CACHE: dict[Path, bytes] = {}


def _read_reference_bytes(path: Path) -> bytes:
    """Read ``path`` once per process; subsequent reads return the cached bytes."""
    cached = _REFERENCE_BYTES_CACHE.get(path)
    if cached is not None:
        return cached
    data = path.read_bytes()
    _REFERENCE_BYTES_CACHE[path] = data
    return data


def _resolved_slot_names(pack: IdentityPack, paths: list[Path]) -> list[str]:
    """Return the slot names that contributed any of the given paths,
    preserving manifest declaration order.
    """
    path_set = set(paths)
    names: list[str] = []
    for slot in pack.slots:
        if any(p in path_set for p in slot.files):
            names.append(slot.name)
    return names


def _load_identity_at_startup() -> None:
    """Populate the identity-pack cache from `settings.identity_dir`.

    Called once at module import so that tool calls see a populated cache
    without needing to `await` a setup step. Failures are non-fatal: the
    loader logs warnings and returns an empty mapping, and this server
    simply proceeds without identity packs (text-only prompts).
    """
    if not settings.identity_enabled:
        return
    try:
        packs = load_identity_packs(settings.identity_dir)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("identity_load_failed error=%s", exc)
        packs = {}
    set_loaded_packs(packs)


_load_identity_at_startup()


def _build_auth():
    """Build auth provider if running in HTTP mode.

    Reads MCP_API_KEY (fleet standard) with fallback to MCP_BILDSPRACHE_API_KEY
    for backwards compatibility. In HTTP mode, a missing/empty key causes the
    server to refuse to start rather than silently run unauthenticated.
    """
    if settings.transport != "http":
        return None

    import os
    api_key = (
        os.getenv("MCP_API_KEY", "")
        or settings.mcp_bildsprache_api_key
    )
    if not api_key:
        raise SystemExit(
            "MCP_API_KEY (or MCP_BILDSPRACHE_API_KEY) is required in HTTP mode. "
            "Refusing to start an unauthenticated server."
        )

    if settings.keycloak_client_secret:
        return create_auth(
            api_key=api_key,
            keycloak_issuer=settings.keycloak_issuer,
            keycloak_audience=settings.keycloak_audience,
            keycloak_client_id=settings.keycloak_client_id,
            keycloak_client_secret=settings.keycloak_client_secret,
            base_url=settings.base_url,
        )

    return BearerTokenVerifier(api_key)


mcp = FastMCP(
    "mcp-bildsprache",
    auth=_build_auth(),
    icons=[
        Icon(
            src="data:image/png;base64,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",
            mimeType="image/png",
            sizes=["96x96"],
        ),
    ],
)


# --- Health endpoint -----------------------------------------------------
from datetime import datetime, timezone as _tz  # noqa: E402
from starlette.requests import Request as _SReq  # noqa: E402
from starlette.responses import JSONResponse as _SResp  # noqa: E402

from mcp_bildsprache import __version__ as _version  # noqa: E402

_start_time = datetime.now(_tz.utc)


@mcp.custom_route("/health", methods=["GET"])
async def _health_check(request: _SReq) -> _SResp:
    return _SResp({
        "status": "healthy",
        "service": "mcp-bildsprache",
        "version": _version,
        "upstream_reachable": True,
        "uptime_seconds": int((datetime.now(_tz.utc) - _start_time).total_seconds()),
    })


@mcp.custom_route("/healthz", methods=["GET"])
async def _health_check_z(request: _SReq) -> _SResp:
    return await _health_check(request)


@mcp.tool
async def generate_image(
    prompt: str,
    context: BrandContext | None = None,
    model: Model | None = None,
    platform: Platform | None = None,
    dimensions: str | None = None,
    mood: str | None = None,
    raw: bool = False,
    reference_images: list[bytes] | None = None,
    include_dogs: bool | None = None,
) -> dict:
    """[image] Generate a brand-aware image.

    Returns a hosted URL by default (when hosting is enabled). The image is
    resized/cropped to exact dimensions, converted to WebP, and stored with
    AI provenance metadata.

    Args:
        prompt: Description of the image to generate.
        context: Brand context (@casey.berlin, @cdit, @storykeep, @nah, @yorizon).
                 If omitted, no brand preset is injected.
        model: Force a specific model (gemini, flux, flux-2-pro, recraft). Auto-routed if omitted.
        platform: Target platform (linkedin-post, blog-hero, etc.) for auto-sizing.
        dimensions: Explicit dimensions as 'WxH' (e.g., '1200x1200'). Overrides platform sizing.
        mood: Emotional register for the image (e.g., 'contemplative', 'energetic').
        raw: If true, also store and return the unprocessed provider image (original format, no resize/WebP) as a separate URL.
        reference_images: Optional list of reference-image bytes. When provided,
            skips the identity pack resolver and forwards the caller's refs
            directly to the provider. Rarely used — identity refs are usually
            auto-resolved from ``context``.
        include_dogs: Override the dog-slot heuristic for ``@casey.berlin``:
            None = use manifest rules (default), True = force-include dog
            slots, False = suppress them. Ignored when ``context`` is not
            ``@casey.berlin`` or no identity pack is loaded.
    """
    # ------------------------------------------------------------------
    # Identity resolution (before routing, so has_references is accurate)
    # ------------------------------------------------------------------
    pack: IdentityPack | None = get_pack_for_context(context)
    resolved_paths: list[Path] = []
    used_identity_pack = False

    if reference_images:
        # Caller-supplied refs bypass the resolver entirely (per spec).
        refs_bytes: list[bytes] = list(reference_images)
    elif pack is not None:
        resolved_paths = resolve_identity_for_call(pack, prompt, include_dogs=include_dogs)
        refs_bytes = [_read_reference_bytes(p) for p in resolved_paths]
        used_identity_pack = bool(refs_bytes)
    else:
        refs_bytes = []

    has_refs = bool(refs_bytes)

    # Determine provider (flux/gemini/recraft) and optional specific model ID
    selected_provider = route_model(
        context=context,
        platform=platform,
        model_hint=model,
        has_references=has_refs,
    )
    specific_model = model if model and model != selected_provider else None

    # Determine dimensions
    if dimensions:
        try:
            parts = dimensions.lower().replace(" ", "").split("x")
            w, h = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            raise ValueError(f"Invalid dimensions '{dimensions}'. Use 'WxH' format, e.g. '1200x630'.")
    elif platform:
        w, h = get_dimensions(platform)
    else:
        w, h = 1200, 1200

    # Build enhanced prompt with brand preset (+ composition clause when
    # an identity pack resolved to a non-empty list for @casey.berlin)
    parts = []
    if context:
        parts.append(get_preset(context))
        if used_identity_pack and context == "@casey.berlin":
            parts.append(CASEY_COMPOSITION_CLAUSE)
    parts.append(prompt)
    if mood:
        parts.append(f"Mood/emotional register: {mood}")
    enhanced_prompt = "\n".join(parts)

    # Generate with fallback
    fallback_used = False
    original_model = None

    async def _call_provider(provider_key: str, model_id: str | None = None) -> ProviderResult:
        provider_fn = PROVIDERS[provider_key]
        kwargs: dict = {}
        if has_refs:
            kwargs["reference_images"] = refs_bytes
        if provider_key == "flux" and model_id:
            return await provider_fn(enhanced_prompt, w, h, model=model_id, **kwargs)
        return await provider_fn(enhanced_prompt, w, h, **kwargs)

    fallback_map = REFERENCE_FALLBACKS if has_refs else FALLBACKS

    try:
        provider_result = await _call_provider(selected_provider, specific_model)
    except Exception as e:
        logger.warning("Provider %s failed: %s — trying fallback", selected_provider, e)
        fallback_provider = fallback_map.get(selected_provider)
        if not fallback_provider:
            raise
        provider_result = await _call_provider(fallback_provider)
        fallback_used = True
        original_model = selected_provider

    # Per-call structured log for identity resolution (INFO).
    if used_identity_pack and pack is not None:
        slot_names = _resolved_slot_names(pack, resolved_paths)
        logger.info(
            "identity_resolved brand=%s slots=%s provider=%s has_include_dogs_override=%s",
            context,
            slot_names,
            provider_result.model,
            include_dogs is not None,
        )

    # Build base response
    result: dict = {
        "model": provider_result.model,
        "cost_estimate": provider_result.cost_estimate,
        "brand_context": context,
        "platform": platform,
        "dimensions": f"{w}x{h}",
    }

    if fallback_used:
        result["fallback_used"] = True
        result["intended_provider"] = original_model
        result["fallback_reason"] = "provider_error"

    # Hosting pipeline: process → store → return hosted URL
    processed_bytes = process_image(
        provider_result=provider_result,
        target_width=w,
        target_height=h,
        prompt=enhanced_prompt,
        brand_context=context,
    )

    hosted_url = store_image(
        image_data=processed_bytes,
        prompt=enhanced_prompt,
        width=w,
        height=h,
        model=provider_result.model,
        cost_estimate=provider_result.cost_estimate,
        brand_context=context,
        fallback_used=fallback_used,
        original_model=original_model,
    )
    result["hosted_url"] = hosted_url

    # Store and return raw (unprocessed) provider output if requested
    if raw:
        try:
            raw_url = store_raw_image(
                image_data=provider_result.image_data,
                mime_type=provider_result.mime_type,
                processed_file_path=result["hosted_url"],
            )
            result["raw_url"] = raw_url
            result["raw_mime_type"] = provider_result.mime_type
        except StorageError as e:
            logger.warning("Failed to store raw image: %s", e)

    result["response_mode"] = "url"
    return result


@mcp.tool
async def generate_prompt(
    prompt: str,
    context: BrandContext | None = None,
    model: Model | None = None,
    platform: Platform | None = None,
    mood: str | None = None,
) -> dict:
    """[image] Generate an engineered image prompt without generating the image.

    Useful for previewing what will be sent to the model, or for manual generation.

    Args:
        prompt: What the image should show.
        context: Brand context for preset injection.
        model: Target model (affects prompt style).
        platform: Target platform (affects dimensions recommendation).
        mood: Emotional register.
    """
    selected_model = route_model(context=context, platform=platform, model_hint=model)

    parts = []
    if context:
        parts.append(get_preset(context))
    parts.append(prompt)
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
async def list_models() -> dict:
    """[image] List available image generation models and their capabilities.

    Returns a mapping with ``providers`` (list of available providers) and
    ``identity_packs`` (brand → bool indicating whether an identity pack is
    currently loaded for that brand).
    """
    available = []

    if settings.gemini_api_key.get_secret_value():
        available.append({
            "id": "gemini",
            "name": "Gemini Image Generation",
            "models": ["gemini-3.1-flash-image-preview", "gemini-2.5-flash-image"],
            "best_for": "Social media graphics, text-on-image, quick iterations",
            "cost": "~$0.01/image",
            "status": "available",
        })

    if settings.bfl_api_key.get_secret_value():
        available.append({
            "id": "flux",
            "name": "FLUX (Black Forest Labs)",
            "models": ["flux-2-max", "flux-2-pro", "flux-kontext-pro", "flux-pro-1.1"],
            "default": "flux-2-max",
            "best_for": "Editorial photography, hero images, cinematic quality, highest fidelity",
            "cost": "$0.03–$0.07/image (model-dependent)",
            "status": "available",
        })

    if settings.recraft_api_key.get_secret_value():
        available.append({
            "id": "recraft",
            "name": "Recraft V4",
            "models": ["recraftv4"],
            "best_for": "Vectors, icons, illustrations, SVG-style output",
            "cost": "$0.04/image",
            "status": "available",
        })

    loaded = get_loaded_packs()
    identity_packs = {brand: True for brand in loaded}

    return {
        "providers": available,
        "identity_packs": identity_packs,
    }


@mcp.tool
async def get_visual_presets(context: BrandContext | None = None) -> dict:
    """[image] Get visual style presets for image generation. For voice/writing rules, use klartext's get_brand_context instead.

    Args:
        context: Specific brand context to retrieve. If omitted, returns all presets.
    """
    loaded = get_loaded_packs()
    if context:
        return {
            "context": context,
            "preset": get_preset(context),
            "identity_pack_loaded": context in loaded,
        }
    return {
        "presets": PRESETS,
        "platforms": PLATFORM_SIZES,
        "identity_packs": {brand: True for brand in loaded},
    }


def _mount_static_files(app) -> None:
    """Mount the image storage directory for static serving."""
    import mimetypes
    from pathlib import Path

    from starlette.staticfiles import StaticFiles

    # Ensure image mime types are registered (python:slim may lack these)
    mimetypes.add_type("image/webp", ".webp")
    mimetypes.add_type("image/avif", ".avif")

    storage_path = Path(settings.image_storage_path)
    storage_path.mkdir(parents=True, exist_ok=True)

    app.mount("/", StaticFiles(directory=str(storage_path)), name="images")
    logger.info("Static file serving enabled at %s", storage_path)


def _mount_gallery(app) -> None:
    """Mount the Tailnet-only gallery sub-app at `/gallery`.

    The TailnetOnlyMiddleware is installed on the parent app so that the
    Host-header check fires for every `/gallery/*` request regardless of
    which sub-app ends up handling it.
    """
    from pathlib import Path

    from mcp_bildsprache.gallery.app import create_gallery_app
    from mcp_bildsprache.gallery.middleware import TailnetOnlyMiddleware

    if not settings.gallery_enabled:
        logger.info("Gallery disabled via settings")
        return

    data_dir = Path(settings.image_storage_path)
    data_dir.mkdir(parents=True, exist_ok=True)

    gallery_app = create_gallery_app(
        data_dir=data_dir,
        public_base_url=settings.image_domain,
        reindex_interval_seconds=settings.gallery_reindex_interval_seconds,
    )

    # Mount BEFORE the `/` static mount runs so the `/gallery` prefix wins.
    app.router.routes.insert(0, _build_gallery_mount(gallery_app))
    app.add_middleware(
        TailnetOnlyMiddleware,
        allowed_host=settings.gallery_tailnet_hostname,
    )
    logger.info(
        "Gallery mounted at /gallery (tailnet hostname: %s)",
        settings.gallery_tailnet_hostname or "<unset>",
    )


def _build_gallery_mount(gallery_app):
    """Factor out the Mount() construction so it's easy to test."""
    from starlette.routing import Mount

    return Mount("/gallery", app=gallery_app)


def main() -> None:
    """Entry point for the mcp-bildsprache server."""
    if settings.transport == "http":
        # stateless_http=True → eliminates orphaned SSE sessions after CF
        # kills idle connections. See openspec mcp-stateless-transport.
        app = mcp.http_app(transport="streamable-http", stateless_http=True)
        _mount_gallery(app)
        _mount_static_files(app)
        import uvicorn

        uvicorn.run(app, host=settings.host, port=settings.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
