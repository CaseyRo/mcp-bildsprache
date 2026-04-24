"""Build ai_attribution v1 payloads for Bildsprache.

CDI-1014 §3. Pure function that takes a ProviderResult plus call context and
returns a dict conforming to ai_attribution.schema.json. Validation is
best-effort — we compute the payload always, but log a warning (not raise)
if validation fails so one bad call never blocks image generation.

The canonical schema + cost table live in CDiT-marketingskills/shared/ and
are mirrored here at mcp_bildsprache/_shared/ (CI diff-check pending in §2).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from mcp_bildsprache._shared.compute_cost import (
    CostResult,
    Usage,
    compute_cost,
    load_cost_table,
)
from mcp_bildsprache.types import ProviderResult

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"
# Mirrors the brand → provider-name map used elsewhere; identity of the
# provider key must match the key under each provider section in
# provider_costs.v1.yaml.
Provider = Literal["openai", "bfl", "gemini", "recraft", "anthropic", "deepl", "other"]
Tier = Literal["standard", "batch", "flex"]


def _shared_path(filename: str) -> Path:
    """Locate a mirrored shared file via importlib.resources.

    Works inside the installed wheel and in editable installs.
    """
    with resources.as_file(resources.files("mcp_bildsprache._shared") / filename) as p:
        return Path(p)


_COST_TABLE: dict[str, Any] | None = None
_SCHEMA: dict[str, Any] | None = None


def _get_cost_table() -> dict[str, Any]:
    global _COST_TABLE
    if _COST_TABLE is None:
        _COST_TABLE = load_cost_table(_shared_path("provider_costs.v1.yaml"))
    return _COST_TABLE


def _get_schema() -> dict[str, Any] | None:
    """Load the JSON Schema. Returns None if jsonschema isn't installed."""
    global _SCHEMA
    if _SCHEMA is None:
        try:
            _SCHEMA = json.loads(_shared_path("ai_attribution.schema.json").read_text())
        except Exception as e:
            logger.warning("Could not load ai_attribution schema: %s", e)
            _SCHEMA = {}
    return _SCHEMA or None


def _provider_key_from_model(model: str) -> Provider:
    """Infer provider key from model id. Used when the caller doesn't pass provider explicitly."""
    if model.startswith("gpt-image"):
        return "openai"
    if model.startswith("flux"):
        return "bfl"
    if model.startswith("gemini"):
        return "gemini"
    if model.startswith("recraft"):
        return "recraft"
    return "other"


def _build_usage(provider_result: ProviderResult) -> dict[str, Any]:
    """Shape the schema-compliant tokens block from provider_result.usage."""
    raw = provider_result.usage or {}
    input_tokens = raw.get("input_tokens")
    output_tokens = raw.get("output_tokens")

    breakdown: dict[str, int | None] = {}
    details = raw.get("input_tokens_details") or {}
    if details:
        breakdown["text_input"] = details.get("text_tokens")
        breakdown["image_input"] = details.get("image_tokens")
        breakdown["cached_input"] = details.get("cached_tokens")
    if output_tokens is not None:
        breakdown.setdefault("image_output", output_tokens)
    if (po := raw.get("partial_image_overhead")) is not None:
        breakdown["partial_image_overhead"] = po
    if (tt := raw.get("thinking_tokens")) is not None:
        breakdown["thinking"] = tt

    return {
        "input": input_tokens,
        "output": output_tokens,
        "units": "tokens",
        **({"breakdown": breakdown} if breakdown else {}),
    }


def _build_cost_block(
    provider: Provider,
    model: str,
    provider_result: ProviderResult,
    tier: Tier,
    image_format: Literal["raster", "vector"],
) -> dict[str, Any]:
    """Compute the cost block using the shared cost table + compute_cost."""
    raw_usage = provider_result.usage or {}
    details = raw_usage.get("input_tokens_details") or {}
    usage = Usage(
        input_tokens=raw_usage.get("input_tokens"),
        cached_input_tokens=details.get("cached_tokens"),
        output_tokens=raw_usage.get("output_tokens"),
    )
    table = _get_cost_table()
    try:
        result: CostResult = compute_cost(
            table=table,
            provider=provider,
            model=model,
            usage=usage,
            tier=tier,
            image_format=image_format,
        )
    except Exception as e:
        # Fall back to the legacy cost_estimate string if table lookup fails.
        logger.warning(
            "compute_cost failed for %s/%s: %s — falling back to provider string",
            provider,
            model,
            e,
        )
        return _legacy_cost_fallback(provider_result, table)

    return {
        "amount_eur": result.amount_eur,
        "method": result.method,
        "source_currency": result.source_currency,
        "source_amount": result.source_amount,
        "fx_rate": result.fx_rate,
        "tier": result.tier,
        "breakdown_usd": result.breakdown_usd,
    }


def _legacy_cost_fallback(
    provider_result: ProviderResult, table: dict[str, Any]
) -> dict[str, Any]:
    """Parse a "$0.03" style string into a cost block when compute_cost can't help."""
    raw = provider_result.cost_estimate or "$0.00"
    usd = 0.0
    try:
        usd = float(raw.lstrip("$").strip())
    except ValueError:
        pass
    fx = float(table.get("fx", {}).get("usd_eur", 1.0))
    return {
        "amount_eur": round(usd * fx, 6),
        "method": "legacy-string",
        "source_currency": "USD",
        "source_amount": usd,
        "fx_rate": fx,
        "tier": "standard",
        "breakdown_usd": {"per_image": usd},
    }


def _build_provenance(
    provider: Provider, provider_result: ProviderResult
) -> dict[str, Any]:
    flags = provider_result.provenance_flags or {}
    return {
        "synthid": bool(flags.get("synthid", provider == "gemini")),
        "c2pa": bool(flags.get("c2pa", False)),
        "exif_signature": flags.get("exif_signature", "bildsprache-v1"),
    }


def _prompt_hash(prompt: str) -> str:
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def build_attribution(
    *,
    provider_result: ProviderResult,
    prompt_anchor: str,
    effective_prompt: str | None = None,
    brand_context: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
    tier: Tier = "standard",
    image_format: Literal["raster", "vector"] = "raster",
) -> dict[str, Any]:
    """Produce an ai_attribution v1 payload.

    Args:
        provider_result: Output from a provider call.
        prompt_anchor: Author-visible prompt (what the caller actually asked for).
        effective_prompt: Full prompt sent to the provider (brand preset + composition
            clauses + mood). Used for the prompt_hash. Falls back to prompt_anchor.
        brand_context: Optional brand context (e.g. "@casey.berlin") — not included
            in the payload directly; carried in outer response fields.
        sources: Optional list of {type, id|href|sha256, title} reference entries.
        params: Optional generation params (size, quality, format, etc.).
        tier: Billing tier for cost math. Default "standard".
        image_format: raster or vector — only relevant for Recraft.

    Returns:
        Dict conforming to ai_attribution.schema.json. Schema validation is
        best-effort: if jsonschema is available we validate and log warnings
        on mismatch; we do NOT raise so a bad schema never blocks generation.
    """
    provider = _provider_key_from_model(provider_result.model)
    effective = effective_prompt or prompt_anchor

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "model": provider_result.model,
        "model_version": provider_result.model_version or provider_result.model,
        "kind": "image",
        "tokens": _build_usage(provider_result),
        "cost": _build_cost_block(
            provider, provider_result.model, provider_result, tier, image_format
        ),
        "prompt_anchor": prompt_anchor,
        "prompt_hash": _prompt_hash(effective),
        "sources": sources or [],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provenance": _build_provenance(provider, provider_result),
    }

    if provider_result.revised_prompt:
        payload["revised_prompt"] = provider_result.revised_prompt
    if params:
        payload["params"] = params

    _validate(payload)
    return payload


def _validate(payload: dict[str, Any]) -> None:
    """Best-effort schema validation. Logs, never raises."""
    schema = _get_schema()
    if not schema:
        return
    try:
        import jsonschema
    except ImportError:
        return
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as e:
        logger.warning(
            "ai_attribution payload failed schema validation: %s",
            e.message,
        )


def format_legacy_cost_estimate(payload: dict[str, Any]) -> str:
    """Derive the legacy 'cost_estimate' display string from the new cost block.

    Preserves the existing response field shape ('€0.049' or '$0.053').
    """
    cost = payload.get("cost", {})
    eur = cost.get("amount_eur")
    if eur is None:
        return "~"
    return f"€{float(eur):.4f}"


def validate_shared_contract() -> None:
    """Eager-load schema + cost table. Raises RuntimeError if either fails.

    Called at server startup for fail-fast behavior — a broken contract
    should prevent the server from booting, not silently emit degraded
    attribution at runtime.
    """
    try:
        _get_cost_table()
    except Exception as e:
        raise RuntimeError(
            f"cost table load failed — refuse to start: {e}"
        ) from e
    schema = _get_schema()
    if not schema:
        raise RuntimeError(
            "ai_attribution schema load failed — refuse to start"
        )


def get_contract_state() -> dict[str, Any]:
    """Return a compact summary of loaded contract state for /health."""
    try:
        table = _get_cost_table()
        cost_table_version = table.get("table_version", "unknown")
        providers_available = sorted(
            k for k in table.keys() if k not in ("fx", "table_version", "snapshot_date")
        )
    except Exception:
        cost_table_version = "load-failed"
        providers_available = []

    schema_version = SCHEMA_VERSION
    try:
        schema = _get_schema()
        if not schema:
            schema_version = "load-failed"
    except Exception:
        schema_version = "load-failed"

    healthy = (
        cost_table_version != "load-failed" and schema_version != "load-failed"
    )
    return {
        "schema_version": schema_version,
        "cost_table_version": cost_table_version,
        "providers_available": providers_available,
        "healthy": healthy,
    }
