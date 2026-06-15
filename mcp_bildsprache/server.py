"""FastMCP server for brand-aware image generation."""

from __future__ import annotations

import logging
from typing import Literal

from fastmcp import Context, FastMCP
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
    CASEY_REGISTER_OVERLAYS,
    PLATFORM_SIZES,
    PRESETS,
    get_dimensions,
    get_preset,
    route_model,
)
from mcp_bildsprache.providers.bfl import generate_bfl
from mcp_bildsprache.providers.gemini import generate_gemini
from mcp_bildsprache.providers.openai import generate_openai
from mcp_bildsprache.providers.recraft import generate_recraft
from mcp_bildsprache.storage import StorageError, store_image, store_raw_image
from mcp_bildsprache.attribution import (
    build_attribution,
    estimate_cost_eur,
    format_legacy_cost_estimate,
    get_contract_state,
    validate_shared_contract,
)
from mcp_bildsprache.models import (
    GenerateDiagramResult,
    GenerateImageResult,
    GeneratePromptResult,
    ModelsResult,
    RecentGenerationsResult,
    VisualPresetsResult,
)
from mcp_bildsprache.types import (
    IdentityPack,
    ProviderResult,
    ProviderTemporarilyDisabled,
)

logger = logging.getLogger(__name__)

Model = Literal[
    # Active providers (May 2026 brand collapse).
    "gemini", "openai", "gpt-image-2", "gpt-image-1.5", "gpt-image-1-mini",
    # Disabled but accepted at the API boundary so the dispatcher can
    # raise ProviderTemporarilyDisabled with a clear migration message.
    # Removing them from the Literal would surface as a cryptic Pydantic
    # ValidationError instead.
    "flux", "flux-2-max", "flux-2-pro", "flux-kontext-pro", "flux-pro-1.1",
    "recraft",
]
Register = Literal["personal", "professional"]
BrandContext = Literal[
    # Active brands (May 2026 brand collapse).
    "casey", "yorizon",
    # Legacy variants — kept for backward-compat; either resolved to
    # 'casey' by normalize_brand or surfaced with a migration message.
    "casey-berlin", "cdit-works", "storykeep", "nah",
    "@casey", "@casey.berlin", "@cdit", "@cdit-works",
    "@storykeep", "@nah", "@yorizon",
]
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
    "openai": generate_openai,
    # FLUX (BFL) and Recraft modules remain in-tree so re-enabling is a
    # one-PR dispatcher swap. The dispatcher does not route to them per
    # the May 2026 brand-collapse change.
    "flux": generate_bfl,
    "recraft": generate_recraft,
}

# Cross-provider fallback chain. Per the May 2026 brand-collapse follow-up
# (2026-05-09), the openai → gemini fallback for raster generation has
# been REMOVED at the user's explicit direction: "It MUST work, no
# fallback! fix before continuing testing." The previous fallback masked
# real OpenAI bugs (e.g. wrong size constraints for gpt-image-1-mini)
# behind silent Gemini handoffs, so callers got Gemini-quality output
# while thinking they had gpt-image-2.
#
# The only fallback that remains is for the diagram path: if Gemini
# fails on generate_diagram, we let the call fail rather than swap to
# OpenAI silently — the caller can opt into OpenAI explicitly via
# model_hint="openai" when sibling-series consistency matters.
FALLBACKS: dict[str, str | None] = {
    "openai": None,
    "gemini": None,
}

# REFERENCE_FALLBACKS kept for callers (tests) that import it; same
# no-fallback policy applies.
REFERENCE_FALLBACKS: dict[str, str | None] = {
    "openai": None,
    "gemini": None,
}


# Module-level cache: reference image bytes are read from disk once per
# process and then reused for every call that resolves to the same file.
# Keyed by absolute Path; populated lazily on first need.
_REFERENCE_BYTES_CACHE: dict[Path, bytes] = {}


# anyio stream-state errors raised when a progress/log notification is
# written to an already-closed/broken streamable-HTTP session. In stateless
# HTTP mode (see `main()`), a long render (gpt-image-2 ~50-80s) can outlive
# the proxy/gateway read timeout; the session's write stream is then closed
# under us, and the *next* ctx.report_progress / ctx.info / ctx.elicit raises
# one of these. Left unguarded it propagates out of the tool body and is
# only caught by mcp's broad `except Exception: logger.exception("Stateless
# session crashed")` in streamable_http_manager — tearing down the session
# and surfacing to the client as -32001 "Request timed out", even though the
# render itself completed and the artifact was written + indexed.
#
# fastmcp 3.4.2 / mcp 1.27.1 do NOT guard this (Context.report_progress and
# Context.log call session.send_* -> _write_stream.send() directly), so the
# guard has to live here. Catching these (a) keeps the still-running tool
# call alive to finish writing the image, and (b) downgrades the lost-write
# to a debug line rather than an ERROR-level crash. `list_recent_generations`
# is then how the caller recovers the URL the timed-out response dropped.
try:  # anyio is a hard transitive dep of fastmcp/mcp; import defensively anyway.
    import anyio as _anyio

    _CLOSED_STREAM_ERRORS: tuple[type[BaseException], ...] = (
        _anyio.ClosedResourceError,
        _anyio.BrokenResourceError,
    )
except Exception:  # pragma: no cover — anyio always present in practice
    _CLOSED_STREAM_ERRORS = ()


async def _progress(ctx: Context | None, progress: float, total: float, message: str) -> None:
    """Report progress over the MCP channel when a Context is present.

    Long generations (native-4K Gemini, OpenAI backoff retries) take 30-60s+
    and look hung to the client without this. No-op when ctx is None (e.g.
    direct unit-test calls), and best-effort: a progress/log failure never
    aborts generation.

    Closed/broken-stream writes (a timed-out streamable-HTTP session, CDI-1253)
    are swallowed at debug specifically — they must NOT bubble up and tear the
    session down, because the render is still running and the artifact will be
    written + indexed (recoverable via `list_recent_generations`).
    """
    if ctx is None:
        return
    try:
        await ctx.report_progress(progress=progress, total=total, message=message)
    except _CLOSED_STREAM_ERRORS:  # pragma: no cover — session closed mid-render
        logger.debug("ctx.report_progress: session stream closed; ignoring", exc_info=True)
    except Exception:  # pragma: no cover — telemetry must never break generation
        logger.debug("ctx.report_progress failed", exc_info=True)


async def _info(ctx: Context | None, message: str) -> None:
    """Emit an info-level log over the MCP channel when a Context is present.

    Guards the same closed/broken-stream case as :func:`_progress` (CDI-1253):
    a ``ctx.info`` write to a timed-out session must not crash the still-running
    tool call.
    """
    if ctx is None:
        return
    try:
        await ctx.info(message)
    except _CLOSED_STREAM_ERRORS:  # pragma: no cover — session closed mid-render
        logger.debug("ctx.info: session stream closed; ignoring", exc_info=True)
    except Exception:  # pragma: no cover — telemetry must never break generation
        logger.debug("ctx.info failed", exc_info=True)


async def _confirm_cost(
    ctx: Context | None,
    *,
    what: str,
    estimated_cost_eur: float | None,
    provider: str,
) -> bool:
    """Defensively elicit a yes/no cost confirmation before a paid call.

    Returns ``True`` to PROCEED with generation, ``False`` to ABORT (the user
    explicitly declined or cancelled).

    Defensive-elicit contract (rule 4): elicitation is an *optional* client
    capability. fastmcp raises when the client/portal has no elicitation
    handler, so any failure here — unsupported, transport error, timeout — is
    treated as "proceed" so paid generation NEVER breaks for clients that
    can't prompt. The ``destructiveHint`` annotation already warns those
    clients that cost is incurred. Only an explicit decline/cancel from a
    client that *does* support elicitation aborts the call.
    """
    if ctx is None:
        # Direct (unit-test / programmatic) calls have no client to prompt.
        return True

    cost_str = (
        f"~€{estimated_cost_eur:.4f}" if estimated_cost_eur is not None else "an unknown amount"
    )
    message = (
        f"{what} via {provider} will call a paid image-generation API and "
        f"incur approximately {cost_str}. Proceed?"
    )
    try:
        result = await ctx.elicit(message, response_type=bool)
    except Exception:
        # Client doesn't support elicitation (or it errored). Proceed — the
        # destructiveHint annotation already surfaced the cost warning.
        logger.debug("cost-confirmation elicitation unavailable; proceeding", exc_info=True)
        return True

    action = getattr(result, "action", None)
    if action == "accept":
        # response_type=bool → result.data is the user's yes/no. A literal
        # "no" (False) is an explicit decline even though the action is
        # "accept". Treat None/missing as accept (proceed).
        data = getattr(result, "data", None)
        return data is not False
    if action in ("decline", "cancel"):
        return False
    # Unknown action shape — fail open (proceed) rather than block on a paid
    # tool the user asked to run.
    return True


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

    api_key = settings.mcp_bildsprache_api_key.get_secret_value()
    if not api_key:
        raise SystemExit(
            "MCP_BILDSPRACHE_API_KEY is required in HTTP mode. "
            "Refusing to start an unauthenticated server."
        )

    keycloak_secret = settings.keycloak_client_secret.get_secret_value()
    if keycloak_secret:
        return create_auth(
            api_key=api_key,
            keycloak_issuer=settings.keycloak_issuer,
            keycloak_audience=settings.keycloak_audience,
            keycloak_client_id=settings.keycloak_client_id,
            keycloak_client_secret=keycloak_secret,
            base_url=settings.base_url,
        )

    return BearerTokenVerifier(api_key)


SERVER_INSTRUCTIONS = """\
Bildsprache turns a text prompt into a brand-aware, hosted image or diagram.
It injects a locked brand visual preset, calls a paid image-generation API,
post-processes (resize/crop -> WebP -> EXIF provenance), and stores the
result, returning a public `hosted_url` plus an `ai_attribution` block with
real EUR cost math and provenance flags.

Choosing a tool:
- `generate_image` — the full raster pipeline. One call resolves identity +
  brand preset + provider routing + sizing + storage + cost attribution.
  Use for social/blog/OG/proposal imagery. Default provider: OpenAI
  gpt-image-2. WRITES an artifact and incurs paid-API cost.
- `generate_diagram` — flow / sequence / state diagrams from free-text or a
  Mermaid source (flowchart/graph, sequenceDiagram, stateDiagram only).
  Default provider: Gemini Nano Banana Pro (best in-image text). WRITES an
  artifact and incurs paid-API cost.
- `generate_prompt` — engineer the brand-injected prompt WITHOUT calling a
  provider or spending money. Use to preview or for manual generation.
- `list_models` — active vs. disabled providers, their costs, and which
  identity packs are loaded.
- `get_visual_presets` — brand visual DNA (palette, register overlays,
  platform sizes). For voice/writing rules use klartext's get_brand_context.

Brand semantics (May 2026 brand collapse): active brands are `casey` (one
voice, two registers — `personal` warmer/kitchen-table, `professional`
crisper/schematic) and `yorizon` (fully isolated, no casey palette tokens).
Legacy keys (casey-berlin, cdit-works, @cdit, storykeep, nah, ...) normalise
to `casey`.

Routing split: raster -> OpenAI gpt-image-2 (Gemini is the cross-provider
fallback target but is NOT auto-selected for raster); diagram -> Gemini Nano
Banana Pro (OpenAI available via model_hint='openai').

No-fallback policy: per an explicit user directive, raster generation has NO
silent provider fallback — if OpenAI fails the error propagates rather than
quietly handing off to Gemini. FLUX and Recraft are temporarily disabled at
the dispatcher; hinting at them returns a PROVIDER_TEMPORARILY_DISABLED error
naming the active replacement.

Reference data (presets, palette, platform sizes, model capabilities) is also
available as cacheable resources under the `bildsprache://` URI scheme.
"""

mcp = FastMCP(
    "mcp-bildsprache",
    instructions=SERVER_INSTRUCTIONS,
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


# Fail-fast: load the ai_attribution schema + provider cost table at import
# time so a broken contract prevents the server from serving, rather than
# silently emitting degraded attribution per-call.
try:
    validate_shared_contract()
except RuntimeError as _contract_err:  # pragma: no cover — start-time fatal
    logger.critical("shared contract broken: %s", _contract_err)
    raise SystemExit(1) from _contract_err


@mcp.custom_route("/health", methods=["GET"])
async def _health_check(request: _SReq) -> _SResp:
    contract = get_contract_state()
    status = "healthy" if contract["healthy"] else "degraded"
    return _SResp({
        "status": status,
        "service": "mcp-bildsprache",
        "version": _version,
        "upstream_reachable": True,
        "uptime_seconds": int((datetime.now(_tz.utc) - _start_time).total_seconds()),
        "attribution": {
            "schema_version": contract["schema_version"],
            "cost_table_version": contract["cost_table_version"],
            "providers_available": contract["providers_available"],
        },
    })


@mcp.custom_route("/healthz", methods=["GET"])
async def _health_check_z(request: _SReq) -> _SResp:
    return await _health_check(request)


@mcp.tool(
    annotations={
        "title": "Generate Brand Image",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def generate_image(
    prompt: str,
    context: BrandContext | None = None,
    register: Register | None = None,
    model: Model | None = None,
    platform: Platform | None = None,
    dimensions: str | None = None,
    mood: str | None = None,
    raw: bool = False,
    reference_images: list[bytes] | None = None,
    include_dogs: bool | None = None,
    draft: bool = False,
    ctx: Context | None = None,
) -> GenerateImageResult:
    """[image] Generate a brand-aware image.

    Returns a hosted URL by default (when hosting is enabled). The image is
    resized/cropped to exact dimensions, converted to WebP, and stored with
    AI provenance metadata.

    Active providers (May 2026 brand collapse): OpenAI gpt-image-2 (default
    raster) and Gemini Nano Banana (fallback). FLUX and Recraft are
    temporarily disabled at the dispatcher; hinting at them raises
    ``PROVIDER_TEMPORARILY_DISABLED``.

    Args:
        prompt: Description of the image to generate.
        context: Brand context. Active brands: ``casey``, ``yorizon``.
                 Legacy variants (``casey-berlin``, ``cdit-works``,
                 ``@cdit``, ...) are normalised to ``casey``. If omitted,
                 no brand preset is injected.
        register: For ``context='casey'`` only. ``personal`` (recognition,
                  manifesto-adjacent) or ``professional`` (verification,
                  workshop voice). Defaults to ``professional`` for casey
                  when omitted.
        model: Force a specific model. Active hints: ``openai``,
                ``gpt-image-2``, ``gpt-image-1-mini``, ``gemini``. Disabled
                hints (``flux``, ``recraft``, ``flux-*``) raise
                PROVIDER_TEMPORARILY_DISABLED with a migration message.
        platform: Target platform (linkedin-post, blog-hero, etc.) for auto-sizing.
        dimensions: Explicit dimensions as 'WxH' (e.g., '1200x1200'). Overrides platform sizing.
        mood: Emotional register for the image (e.g., 'contemplative', 'energetic').
        raw: If true, also store and return the unprocessed provider image (original format, no resize/WebP) as a separate URL.
        reference_images: Optional list of reference-image bytes. When provided,
            skips the identity pack resolver and forwards the caller's refs
            directly to the provider.
        include_dogs: Override the dog-slot heuristic for ``casey``:
            None = use manifest rules (default), True = force-include dog
            slots (Sien, Fimme), False = suppress them. Ignored when no
            identity pack is loaded for the resolved context.
        draft: If true, route OpenAI to ``gpt-image-1-mini`` (cheap tier).
                Trades quality for cost.
    """
    # ------------------------------------------------------------------
    # Identity resolution (before routing, so has_references is accurate)
    # ------------------------------------------------------------------
    await _progress(ctx, 0, 5, "Resolving identity and brand context")
    pack: IdentityPack | None = get_pack_for_context(context)
    resolved_paths: list[Path] = []
    used_identity_pack = False

    if reference_images:
        # Caller-supplied refs bypass the resolver entirely (per spec).
        refs_bytes: list[bytes] = list(reference_images)
        await _info(ctx, f"Using {len(refs_bytes)} caller-supplied reference image(s)")
    elif pack is not None:
        resolved_paths = resolve_identity_for_call(pack, prompt, include_dogs=include_dogs)
        refs_bytes = [_read_reference_bytes(p) for p in resolved_paths]
        used_identity_pack = bool(refs_bytes)
        if used_identity_pack:
            await _info(
                ctx,
                f"Resolved identity pack '{context}' -> "
                f"{len(refs_bytes)} reference slot(s)",
            )
    else:
        refs_bytes = []

    has_refs = bool(refs_bytes)

    # Determine provider. Per the May 2026 brand collapse, FLUX/Recraft
    # hints raise ProviderTemporarilyDisabled; surface as a clean MCP
    # error rather than a 500.
    await _progress(ctx, 1, 5, "Routing to image provider")
    try:
        selected_provider = route_model(
            context=context,
            platform=platform,
            model_hint=model,
            has_references=has_refs,
            intent="raster",
        )
    except ProviderTemporarilyDisabled as e:
        await _info(ctx, f"Provider hint rejected: {e.message}")
        return {
            "error": {
                "code": "PROVIDER_TEMPORARILY_DISABLED",
                "provider": e.provider,
                "replacement": e.replacement,
                "message": e.message,
            },
        }
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
    # an identity pack resolved to a non-empty list for the casey brand).
    parts = []
    if context:
        parts.append(get_preset(context, register=register))
        # Casey composition clause fires when an identity pack contributed
        # to refs and the resolved canonical brand is 'casey' (covers all
        # legacy aliases via normalize_brand).
        from mcp_bildsprache.brands import normalize_brand as _normalize_brand
        if used_identity_pack and _normalize_brand(context) == "casey":
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
        if provider_key == "openai":
            # OpenAI provider accepts draft to route to gpt-image-1-mini.
            # If the caller pinned an explicit gpt-image-* via model, pass it through.
            kwargs["draft"] = draft
            if model_id and model_id.startswith("gpt-image"):
                kwargs["model"] = model_id
            return await provider_fn(enhanced_prompt, w, h, **kwargs)
        if provider_key == "flux" and model_id:
            return await provider_fn(enhanced_prompt, w, h, model=model_id, **kwargs)
        return await provider_fn(enhanced_prompt, w, h, **kwargs)

    fallback_map = REFERENCE_FALLBACKS if has_refs else FALLBACKS

    # Cost-confirmation gate (defensive elicit). Surface the estimated EUR
    # cost — the same compute_cost math the post-call ai_attribution uses —
    # before spending money. No-op for clients without elicitation support.
    est_cost = estimate_cost_eur(
        provider=selected_provider,
        model=specific_model,
        width=w,
        height=h,
    )
    if not await _confirm_cost(
        ctx, what="Generating this image", estimated_cost_eur=est_cost, provider=selected_provider
    ):
        await _info(ctx, "Image generation cancelled by user at cost confirmation")
        return {
            "cancelled": True,
            "response_mode": "cancelled",
            "brand_context": context,
            "platform": platform,
            "dimensions": f"{w}x{h}",
            "model": specific_model or selected_provider,
            "estimated_cost_eur": est_cost,
        }

    await _progress(
        ctx, 2, 5, f"Generating image via {selected_provider} (this can take 30-60s)"
    )
    try:
        provider_result = await _call_provider(selected_provider, specific_model)
    except Exception as e:
        logger.warning("Provider %s failed: %s — trying fallback", selected_provider, e)
        fallback_provider = fallback_map.get(selected_provider)
        if not fallback_provider:
            raise
        await _info(
            ctx, f"{selected_provider} failed; falling back to {fallback_provider}"
        )
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

    # Build the canonical ai_attribution payload (CDI-1014 §3). Done before
    # the response dict so we can derive the legacy cost_estimate from it.
    attribution = build_attribution(
        provider_result=provider_result,
        prompt_anchor=prompt,
        effective_prompt=enhanced_prompt,
        brand_context=context,
        params={
            "platform": platform,
            "dimensions": f"{w}x{h}",
        },
    )

    # Build base response. `cost_estimate` stays for backward-compat but is
    # now derived from attribution.cost.amount_eur so it reflects the real
    # EUR figure, not the provider's raw USD string.
    result: dict = {
        "model": provider_result.model,
        "cost_estimate": format_legacy_cost_estimate(attribution),
        "brand_context": context,
        "platform": platform,
        "dimensions": f"{w}x{h}",
        "ai_attribution": attribution,
    }

    if fallback_used:
        result["fallback_used"] = True
        result["intended_provider"] = original_model
        result["fallback_reason"] = "provider_error"

    # Hosting pipeline: process → store → return hosted URL
    await _progress(ctx, 3, 5, "Post-processing image (resize, crop, WebP, EXIF)")
    processed_bytes = process_image(
        provider_result=provider_result,
        target_width=w,
        target_height=h,
        prompt=enhanced_prompt,
        brand_context=context,
    )

    await _progress(ctx, 4, 5, "Storing image and writing provenance sidecar")
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
        attribution=attribution,
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

    # CDI-1014 §11.3 — structured log line per image generation.
    # One JSON line, no prompt/image content. Enables cost aggregation
    # via Komodo log queries and bildsprache cost trends over time.
    cost_block = attribution.get("cost", {})
    logger.info(
        "event=image_generated "
        "provider=%s model=%s brand_context=%s amount_eur=%.6f "
        "tier=%s schema_version=%s draft=%s fallback=%s",
        attribution.get("provider"),
        provider_result.model,
        context or "none",
        float(cost_block.get("amount_eur") or 0.0),
        cost_block.get("tier", "standard"),
        attribution.get("schema_version"),
        draft,
        fallback_used,
    )

    await _progress(ctx, 5, 5, "Done")
    return result


DiagramFormatLiteral = Literal["flow", "sequence", "state"]
DiagramModelHint = Literal["openai", "gemini", "gpt-image-2", "nano-banana-pro"]


@mcp.tool(
    annotations={
        "title": "Generate Brand Diagram",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def generate_diagram(
    format: DiagramFormatLiteral,
    prompt: str | None = None,
    mermaid: str | None = None,
    register: Register = "professional",
    model_hint: DiagramModelHint | None = None,
    dimensions: str | None = None,
    ctx: Context | None = None,
) -> GenerateDiagramResult:
    """[image] Generate a brand-aware diagram (flow / sequence / state).

    Default routing is Gemini Nano Banana Pro — best in-image text legibility,
    pre-render "thinking" composition, native 4K output. OpenAI gpt-image-2
    is available via ``model_hint='openai'`` (sibling-series consistency,
    reference-image support). FLUX/Recraft hints raise
    PROVIDER_TEMPORARILY_DISABLED.

    Exactly one of ``prompt`` or ``mermaid`` must be provided.

    Args:
        format: Diagram type — ``flow`` (flowchart), ``sequence`` (UML
                sequence diagram), or ``state`` (UML state diagram).
                v1 supports these three; other Mermaid types
                (classDiagram, erDiagram, gantt, pie, etc.) are rejected.
        prompt: Free-text description of the diagram. Use this when you
                want the model to compose structure from a natural-language
                spec.
        mermaid: Mermaid source. Parsed into a structured render brief —
                 deterministic structure, brand-locked rendering. Mermaid
                 source must start with ``flowchart``/``graph``/
                 ``sequenceDiagram``/``stateDiagram`` (case-insensitive).
        register: ``personal`` (warmer, hand-drawn quality, looser composition)
                  or ``professional`` (crisper, schematic, restrained).
                  Defaults to ``professional`` (the typical diagram use-case).
        model_hint: Force a specific provider/model. ``gemini`` (default),
                    ``openai``, ``gpt-image-2``, ``nano-banana-pro``. FLUX
                    and Recraft are rejected.
        dimensions: Explicit ``WxH`` (e.g. ``'1600x900'``). Defaults to
                    ``1600x900`` for flow/state and ``1200x1600`` for sequence.

    Returns:
        Hosted URL plus ai_attribution, dimensions, model. Reuses the same
        storage + sidecar + gallery pipeline as generate_image — diagrams
        land under ``casey/`` for register=personal/professional.
    """
    from mcp_bildsprache.diagrams import (
        MermaidParseError,
        compose_render_brief,
        parse_mermaid,
    )

    if not prompt and not mermaid:
        return {
            "error": {
                "code": "INVALID_INPUT",
                "message": "Provide exactly one of `prompt` or `mermaid`.",
            },
        }
    if prompt and mermaid:
        return {
            "error": {
                "code": "INVALID_INPUT",
                "message": "Provide either `prompt` OR `mermaid`, not both.",
            },
        }

    await _progress(ctx, 0, 4, "Parsing diagram spec")
    parsed = None
    if mermaid:
        try:
            parsed = parse_mermaid(mermaid)
        except MermaidParseError as e:
            return {
                "error": {
                    "code": "MERMAID_PARSE_ERROR",
                    "message": str(e),
                    "line": e.line,
                },
            }
        if parsed.format != format:
            return {
                "error": {
                    "code": "MERMAID_FORMAT_MISMATCH",
                    "message": (
                        f"Mermaid header is {parsed.format!r} but format "
                        f"argument is {format!r}. Pass format='{parsed.format}' "
                        "or use a Mermaid source that matches."
                    ),
                },
            }

    # Route to provider (default gemini for diagrams).
    await _progress(ctx, 1, 4, "Routing to diagram provider")
    try:
        selected_provider = route_model(
            model_hint=model_hint,
            intent="diagram",
        )
    except ProviderTemporarilyDisabled as e:
        await _info(ctx, f"Provider hint rejected: {e.message}")
        return {
            "error": {
                "code": "PROVIDER_TEMPORARILY_DISABLED",
                "provider": e.provider,
                "replacement": e.replacement,
                "message": e.message,
            },
        }
    except ValueError as e:
        return {
            "error": {
                "code": "INVALID_MODEL_HINT",
                "message": str(e),
            },
        }

    specific_model = (
        model_hint if model_hint and model_hint != selected_provider else None
    )

    # Compose engineered prompt (palette + register + format conventions).
    enhanced_prompt = compose_render_brief(
        parsed=parsed,
        prompt=prompt,
        format=format,
        register=register,
    )

    # Resolve dimensions. Sequence diagrams render better tall.
    if dimensions:
        try:
            d_parts = dimensions.lower().replace(" ", "").split("x")
            w, h = int(d_parts[0]), int(d_parts[1])
        except (ValueError, IndexError):
            return {
                "error": {
                    "code": "INVALID_DIMENSIONS",
                    "message": (
                        f"Invalid dimensions {dimensions!r}. Use 'WxH' "
                        "(e.g. '1600x900')."
                    ),
                },
            }
    elif format == "sequence":
        w, h = 1200, 1600
    else:
        w, h = 1600, 900

    # Dispatch with fallback (openai ↔ gemini for diagrams).
    fallback_used = False
    original_provider = None

    async def _call_provider(provider_key: str, model_id: str | None = None) -> ProviderResult:
        provider_fn = PROVIDERS[provider_key]
        kwargs: dict = {}
        if provider_key == "openai":
            if model_id and model_id.startswith("gpt-image"):
                kwargs["model"] = model_id
            return await provider_fn(enhanced_prompt, w, h, **kwargs)
        return await provider_fn(enhanced_prompt, w, h, **kwargs)

    # Cost-confirmation gate (defensive elicit) before the paid render.
    est_cost = estimate_cost_eur(
        provider=selected_provider,
        model=specific_model,
        width=w,
        height=h,
    )
    if not await _confirm_cost(
        ctx, what="Rendering this diagram", estimated_cost_eur=est_cost, provider=selected_provider
    ):
        await _info(ctx, "Diagram generation cancelled by user at cost confirmation")
        return {
            "cancelled": True,
            "response_mode": "cancelled",
            "brand_context": "casey",
            "register": register,
            "format": format,
            "dimensions": f"{w}x{h}",
            "model": specific_model or selected_provider,
            "estimated_cost_eur": est_cost,
        }

    await _progress(
        ctx, 2, 4, f"Rendering diagram via {selected_provider} (this can take 30-60s)"
    )
    try:
        provider_result = await _call_provider(selected_provider, specific_model)
    except Exception as e:
        logger.warning(
            "Diagram provider %s failed: %s — trying fallback",
            selected_provider,
            e,
        )
        fallback_provider = FALLBACKS.get(selected_provider)
        if not fallback_provider:
            raise
        await _info(
            ctx, f"{selected_provider} failed; falling back to {fallback_provider}"
        )
        provider_result = await _call_provider(fallback_provider)
        fallback_used = True
        original_provider = selected_provider

    # Diagrams always carry the casey brand context (yorizon-isolated per
    # the diagram-generation spec). Build attribution accordingly.
    attribution = build_attribution(
        provider_result=provider_result,
        prompt_anchor=(prompt or "(mermaid input)"),
        effective_prompt=enhanced_prompt,
        brand_context="casey",
        params={
            "platform": "diagram",
            "format": format,
            "register": register,
            "dimensions": f"{w}x{h}",
        },
    )

    result: dict = {
        "model": provider_result.model,
        "cost_estimate": format_legacy_cost_estimate(attribution),
        "brand_context": "casey",
        "register": register,
        "format": format,
        "dimensions": f"{w}x{h}",
        "ai_attribution": attribution,
    }
    if fallback_used:
        result["fallback_used"] = True
        result["intended_provider"] = original_provider
        result["fallback_reason"] = "provider_error"

    await _progress(ctx, 3, 4, "Post-processing and storing diagram")
    processed_bytes = process_image(
        provider_result=provider_result,
        target_width=w,
        target_height=h,
        prompt=enhanced_prompt,
        brand_context="casey",
    )

    hosted_url = store_image(
        image_data=processed_bytes,
        prompt=enhanced_prompt,
        width=w,
        height=h,
        model=provider_result.model,
        cost_estimate=provider_result.cost_estimate,
        brand_context="casey",
        fallback_used=fallback_used,
        original_model=original_provider,
        attribution=attribution,
    )
    result["hosted_url"] = hosted_url
    result["response_mode"] = "url"

    # Structured log for cost aggregation, mirrors generate_image.
    cost_block = attribution.get("cost", {})
    logger.info(
        "event=diagram_generated "
        "provider=%s model=%s format=%s register=%s amount_eur=%.6f "
        "tier=%s schema_version=%s fallback=%s",
        attribution.get("provider"),
        provider_result.model,
        format,
        register,
        float(cost_block.get("amount_eur") or 0.0),
        cost_block.get("tier", "standard"),
        attribution.get("schema_version"),
        fallback_used,
    )

    await _progress(ctx, 4, 4, "Done")
    return result


@mcp.tool(
    annotations={
        "title": "Engineer Image Prompt",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def generate_prompt(
    prompt: str,
    context: BrandContext | None = None,
    register: Register | None = None,
    model: Model | None = None,
    platform: Platform | None = None,
    mood: str | None = None,
) -> GeneratePromptResult:
    """[image] Generate an engineered image prompt without generating the image.

    Useful for previewing what will be sent to the model, or for manual generation.

    Args:
        prompt: What the image should show.
        context: Brand context for preset injection.
        register: For ``context='casey'`` only — ``personal`` or ``professional``.
        model: Target model (affects prompt style). FLUX/Recraft hints raise
                PROVIDER_TEMPORARILY_DISABLED.
        platform: Target platform (affects dimensions recommendation).
        mood: Emotional register.
    """
    try:
        selected_model = route_model(
            context=context, platform=platform, model_hint=model, intent="raster"
        )
    except ProviderTemporarilyDisabled as e:
        return {
            "error": {
                "code": "PROVIDER_TEMPORARILY_DISABLED",
                "provider": e.provider,
                "replacement": e.replacement,
                "message": e.message,
            },
        }

    parts = []
    if context:
        parts.append(get_preset(context, register=register))
    parts.append(prompt)
    if mood:
        parts.append(f"Mood/emotional register: {mood}")

    dimensions = get_dimensions(platform) if platform else (1200, 1200)

    return {
        "engineered_prompt": "\n".join(parts),
        "model": selected_model,
        "dimensions": f"{dimensions[0]}x{dimensions[1]}",
        "brand_context": context,
        "register": register,
        "platform": platform,
    }


@mcp.tool(
    annotations={
        "title": "List Image Models",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def list_models() -> ModelsResult:
    """[image] List active image generation providers and their capabilities.

    Returns a mapping with ``providers`` (active providers reachable via the
    dispatcher), ``disabled_providers`` (modules in-tree but not dispatched
    per the May 2026 brand collapse), and ``identity_packs`` (brand → bool
    indicating whether an identity pack is currently loaded).
    """
    from mcp_bildsprache.presets import DISABLED_PROVIDERS

    available = []

    if settings.openai_api_key.get_secret_value():
        available.append({
            "id": "openai",
            "name": "OpenAI gpt-image-2",
            "models": [
                settings.openai_image_model,
                settings.openai_image_model_draft,
            ],
            "default": settings.openai_image_model,
            "best_for": (
                "Default raster path. Strong typography in-image, "
                "sibling-series consistency, reference image support."
            ),
            "cost": "$0.006–$0.211/image (quality-dependent)",
            "rate_limit": "Tier 1: 5 IPM / 100K TPM (sequential dispatch)",
            "status": "available",
        })

    if settings.gemini_api_key.get_secret_value():
        available.append({
            "id": "gemini",
            "name": "Gemini Nano Banana",
            "models": [
                "gemini-3.1-flash-image-preview",
                "gemini-2.5-flash-image",
            ],
            "best_for": (
                "Diagram path (default for generate_diagram). Best in-image "
                "text legibility, 'thinking' pre-render, native 4K. Also "
                "the raster fallback when OpenAI is unavailable."
            ),
            "cost": "~$0.039/image (flat-rate flash tier)",
            "status": "available",
        })

    loaded = get_loaded_packs()
    identity_packs = {brand: True for brand in loaded}

    return {
        "providers": available,
        "disabled_providers": list(DISABLED_PROVIDERS),
        "identity_packs": identity_packs,
        "diagram_capable": ["openai", "gemini"],
        "diagram_formats": ["flow", "sequence", "state"],
    }


@mcp.tool(
    annotations={
        "title": "List Recent Generations",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def list_recent_generations(
    limit: int = 20,
    offset: int = 0,
    brand: BrandContext | None = None,
) -> RecentGenerationsResult:
    """[image] List the most recently generated images (newest first).

    Reads the on-disk artifact index (the same sidecar metadata the gallery
    serves) under the image storage directory and returns each artifact's
    public ``hosted_url`` plus metadata (timestamp, dimensions, brand, model,
    cost, and the stored prompt).

    Recovery use-case: ``generate_image`` / ``generate_diagram`` renders take
    30-80s. If the streamable-HTTP session times out mid-render, the image is
    still written + indexed server-side but the caller never receives the URL.
    This tool surfaces those completed-but-orphaned artifacts so the hosted
    URL is recoverable without re-running (and re-paying for) the generation.

    Does NOT call any provider and incurs no cost — it only reads local
    metadata. An empty result (``total == 0``) is a clean response, not an
    error.

    Args:
        limit: Max artifacts to return (1-500). Clamped into range; values
               below 1 yield an empty page (with the full ``total`` still
               reported). Defaults to 20.
        offset: Number of (filtered, newest-first) artifacts to skip before
                the page. Defaults to 0.
        brand: Optional brand filter. Active brands: ``casey``, ``yorizon``.
               Legacy variants are normalised to their stored directory, so
               e.g. ``casey-berlin`` also matches the ``casey-berlin/`` dir.
               When omitted, artifacts across all brands are returned.
    """
    from mcp_bildsprache.brands import normalize_brand
    from mcp_bildsprache.gallery.index import GalleryIndex

    # Build a fresh index off the live storage dir. This is the same cheap
    # filesystem walk the gallery runs on every reindex tick, so it always
    # reflects artifacts written since the gallery's last timer tick —
    # including a render whose response was lost to a session timeout.
    index = GalleryIndex(
        data_dir=Path(settings.image_storage_path),
        public_base_url=settings.image_domain,
    )
    index.refresh()

    # Match the caller's brand against stored directory names. New artifacts
    # land under the normalized brand dir (e.g. 'casey'); legacy directories
    # ('casey-berlin', 'cdit') are matched verbatim. Include both the raw and
    # normalized keys so either form resolves.
    brand_filter: list[str] | None = None
    if brand:
        candidates = {brand, normalize_brand(brand)}
        brand_filter = [b for b in candidates if b]

    total, entries = index.filter_and_sort(
        brand=brand_filter,
        sort="created_desc",
        limit=limit,
        offset=offset,
    )

    generations = [e.to_public_dict() for e in entries]
    # Effective (clamped) page size, mirroring GalleryIndex.filter_and_sort.
    effective_limit = max(0, min(limit, 500))
    effective_offset = max(0, offset)

    return {
        "generations": generations,
        "total": total,
        "returned": len(generations),
        "limit": effective_limit,
        "offset": effective_offset,
        "brand": brand,
    }


@mcp.tool(
    annotations={
        "title": "Get Visual Presets",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def get_visual_presets(
    context: BrandContext | None = None,
    register: Register | None = None,
) -> VisualPresetsResult:
    """[image] Get visual style presets for image generation. For voice/writing rules, use klartext's get_brand_context instead.

    Active brands (May 2026 brand collapse): ``casey`` (with personal/professional
    registers), ``yorizon``. Legacy keys resolve to ``casey``.

    Args:
        context: Specific brand context to retrieve. If omitted, returns all presets.
        register: For ``context='casey'`` only. ``personal`` or ``professional``.
                  When provided, the response preset includes the matching
                  register overlay.
    """
    from mcp_bildsprache.presets import CASEY_REGISTER_OVERLAYS

    loaded = get_loaded_packs()
    if context:
        return {
            "context": context,
            "register": register,
            "preset": get_preset(context, register=register),
            "identity_pack_loaded": (
                context in loaded
                or any(b in loaded for b in ("casey", "@casey", "@casey.berlin", "casey-berlin"))
                if context in {"casey", "@casey", "casey-berlin", "@casey.berlin"}
                else context in loaded
            ),
        }
    return {
        "presets": PRESETS,
        "casey_register_overlays": CASEY_REGISTER_OVERLAYS,
        "platforms": PLATFORM_SIZES,
        "identity_packs": {brand: True for brand in loaded},
    }


# --- Resources: brand reference data ------------------------------------
#
# Reference data (presets, palette, platform sizes, model capabilities,
# diagram formats, contract/status) is exposed as cacheable, citable
# resources under the `bildsprache://` URI scheme. These mirror the same
# in-memory sources the read tools serve, so a client can fetch context
# once and cache it instead of round-tripping a tool call per lookup.


@mcp.resource(
    "bildsprache://presets",
    name="Brand visual presets",
    description="All brand visual presets plus the casey personal/professional register overlays.",
    mime_type="application/json",
)
def resource_presets() -> dict:
    """Full PRESETS map + casey register overlays (visual DNA per brand)."""
    return {
        "presets": PRESETS,
        "casey_register_overlays": CASEY_REGISTER_OVERLAYS,
    }


@mcp.resource(
    "bildsprache://palette/casey",
    name="Casey botanical palette",
    description="The locked casey botanical colour palette (hex/oklch/role) from the May 2026 brand-decisions doc.",
    mime_type="application/json",
)
def resource_casey_palette() -> dict:
    """Locked casey palette tokens (paper bone, forest moss, ...)."""
    from mcp_bildsprache.presets import CASEY_PALETTE

    return {"brand": "casey", "palette": CASEY_PALETTE}


@mcp.resource(
    "bildsprache://platforms",
    name="Platform sizes",
    description="Platform -> (width, height) auto-sizing table used by generate_image.",
    mime_type="application/json",
)
def resource_platforms() -> dict:
    """Platform sizing table (linkedin-post, blog-hero, og-image, ...)."""
    return {
        "platforms": {name: list(size) for name, size in PLATFORM_SIZES.items()},
    }


@mcp.resource(
    "bildsprache://models",
    name="Image model capabilities",
    description="Active providers + costs, disabled providers, loaded identity packs, and diagram capabilities.",
    mime_type="application/json",
)
async def resource_models() -> dict:
    """Same payload as the list_models tool, addressable as a resource."""
    return await list_models.fn()


@mcp.resource(
    "bildsprache://status",
    name="Server status",
    description="Server version, uptime, and the shared ai_attribution schema + cost-table contract state.",
    mime_type="application/json",
)
def resource_status() -> dict:
    """Contract + version status (mirrors the /health endpoint body)."""
    contract = get_contract_state()
    return {
        "service": "mcp-bildsprache",
        "version": _version,
        "status": "healthy" if contract["healthy"] else "degraded",
        "uptime_seconds": int(
            (datetime.now(_tz.utc) - _start_time).total_seconds()
        ),
        "attribution": {
            "schema_version": contract["schema_version"],
            "cost_table_version": contract["cost_table_version"],
            "providers_available": contract["providers_available"],
        },
    }


# --- Prompts: guided multi-step workflows -------------------------------
#
# These encode the parameter combinatorics of the signature jobs so the
# client follows the intended path (brand -> register -> platform -> mood,
# or Mermaid -> register -> diagram) instead of rediscovering it per call.


@mcp.prompt(
    name="brand_image_brief",
    description="Guided brief for a branded image: walks brand -> register -> platform -> mood, then calls generate_image.",
)
def brand_image_brief(
    subject: str,
    brand: str = "casey",
    register: str = "professional",
    platform: str = "linkedin-post",
    mood: str = "",
) -> str:
    """Compose a brand_image_brief guidance message for generate_image."""
    mood_line = (
        f"- Mood/emotional register: {mood}\n"
        if mood
        else "- Mood: infer from the subject if helpful, else omit\n"
    )
    return (
        "Create a brand-aware image with the `generate_image` tool.\n\n"
        "Decision path (fill each, then call the tool once):\n"
        f"- Subject: {subject}\n"
        f"- context (brand): {brand}  "
        "(active brands: casey, yorizon; legacy keys normalise to casey)\n"
        f"- register: {register}  "
        "(casey only — personal = warmer/kitchen-table, professional = crisper/schematic)\n"
        f"- platform: {platform}  "
        "(drives auto-sizing; or pass explicit dimensions='WxH')\n"
        f"{mood_line}"
        "\nBefore generating you may preview the engineered prompt with "
        "`generate_prompt` (no provider call, no cost). Consult "
        "`bildsprache://presets` and `bildsprache://platforms` for the exact "
        "brand DNA and sizes. Then call `generate_image` and report the "
        "returned hosted_url and ai_attribution cost."
    )


@mcp.prompt(
    name="mermaid_to_diagram",
    description="Guided workflow: take Mermaid source, pick a register, and render a brand-locked diagram via generate_diagram.",
)
def mermaid_to_diagram(
    mermaid: str,
    format: str = "flow",
    register: str = "professional",
) -> str:
    """Compose a mermaid_to_diagram guidance message for generate_diagram."""
    return (
        "Render the following Mermaid source into a brand-locked diagram with "
        "the `generate_diagram` tool.\n\n"
        f"Mermaid source:\n```\n{mermaid.strip()}\n```\n\n"
        "Decision path:\n"
        f"- format: {format}  "
        "(must match the Mermaid header — flow|sequence|state; other Mermaid "
        "types are rejected)\n"
        f"- register: {register}  "
        "(personal = warmer/hand-drawn, professional = crisper/schematic)\n"
        "- Default provider is Gemini Nano Banana Pro (best in-image text); "
        "pass model_hint='openai' only if sibling-series consistency matters.\n\n"
        "Call `generate_diagram(format=..., mermaid=..., register=...)` and "
        "report the returned hosted_url. If you get MERMAID_FORMAT_MISMATCH, "
        "set format to the value named in the error."
    )


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

    Lifespan wiring (2026-05-09 fix): FastMCP's ``http_app()`` provides
    its own parent lifespan that does NOT delegate to mounted sub-apps,
    so the gallery's ``create_gallery_app`` lifespan never fires by
    default — meaning ``GalleryIndex.refresh()`` never runs at startup
    and ``_reindex_loop`` never gets scheduled. The gallery responds to
    requests but the index is empty until someone POSTs ``/api/reindex``
    manually.

    Fix: drive the gallery's startup/shutdown ourselves from this mount
    helper. Initial scan runs synchronously (filesystem walk, no event
    loop required); the periodic reindex task is started inside a
    wrapper around the parent app's existing lifespan so it survives
    FastMCP's ASGI shape.
    """
    import asyncio
    import contextlib
    from contextlib import asynccontextmanager
    from pathlib import Path

    from mcp_bildsprache.gallery.app import create_gallery_app
    from mcp_bildsprache.gallery.index import _reindex_loop
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

    # Initial scan: synchronous filesystem walk, runs before uvicorn binds.
    # This guarantees /gallery/api/images returns populated data on the
    # very first request, regardless of how the parent lifespan behaves.
    index = gallery_app.state.gallery_index
    initial_count = index.refresh()
    logger.info(
        "Gallery initial scan: %d entries from %s",
        initial_count,
        data_dir,
    )

    # Mount BEFORE the `/` static mount runs so the `/gallery` prefix wins.
    app.router.routes.insert(0, _build_gallery_mount(gallery_app))
    app.add_middleware(
        TailnetOnlyMiddleware,
        allowed_host=settings.gallery_tailnet_hostname,
    )

    # Wrap the parent's lifespan so the periodic reindex task starts and
    # stops with the parent's ASGI lifespan. Doing this AFTER mounting
    # ensures we wrap whatever FastMCP put in place.
    interval_s = max(1, int(settings.gallery_reindex_interval_seconds))
    parent_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _wrapped_lifespan(scope_app):
        async with parent_lifespan(scope_app):
            task = asyncio.create_task(_reindex_loop(index, interval_s))
            logger.info(
                "Gallery periodic reindex started (interval %ds)",
                interval_s,
            )
            try:
                yield
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app.router.lifespan_context = _wrapped_lifespan

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

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        uvicorn.run(app, host=settings.host, port=settings.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
