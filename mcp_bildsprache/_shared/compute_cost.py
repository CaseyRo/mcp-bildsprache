"""Cost computation for AI attribution.

Pure function surface — no I/O, no state. Load the pricing table once via
load_cost_table() and pass it into compute_cost() per call.

Consumers (Bildsprache, Klartext, Writings) mirror this module into their own
shared/ folder and call it from their attribution builder. CI diffs the mirror
against the canonical file to prevent drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

Mode = Literal["per-token-usage", "per-image", "per-token"]
Tier = Literal["standard", "batch", "flex"]


@dataclass(frozen=True)
class Usage:
    """Provider-reported usage. Sub-fields are best-effort and nullable."""

    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    partial_image_tokens: int | None = None


@dataclass(frozen=True)
class CostResult:
    amount_eur: float
    source_amount: float
    source_currency: str
    fx_rate: float
    method: str
    tier: Tier
    breakdown_usd: dict[str, float]


def load_cost_table(path: str | Path) -> dict[str, Any]:
    """Load provider_costs.v*.yaml. Raises on missing/malformed file."""
    with open(path, "r", encoding="utf-8") as f:
        table = yaml.safe_load(f)
    if not isinstance(table, dict):
        raise ValueError(f"Cost table {path} is not a mapping")
    for required in ("table_version", "fx"):
        if required not in table:
            raise ValueError(f"Cost table {path} missing required key '{required}'")
    if "usd_eur" not in table["fx"]:
        raise ValueError(f"Cost table {path} missing fx.usd_eur")
    return table


def compute_cost(
    *,
    table: dict[str, Any],
    provider: str,
    model: str,
    usage: Usage | None = None,
    tier: Tier = "standard",
    image_format: Literal["raster", "vector"] = "raster",
) -> CostResult:
    """Compute EUR cost for one generation.

    Args:
        table: Loaded cost table (from load_cost_table).
        provider: Provider key in the table (e.g. "openai").
        model: Model key under the provider (e.g. "gpt-image-2").
        usage: Provider-reported usage. Required for per-token-usage modes.
        tier: Billing tier. Applies batch_discount when "batch".
        image_format: For Recraft, distinguishes raster/vector pricing.

    Returns:
        CostResult with amount_eur and reproducible breakdown.
    """
    if provider not in table:
        raise KeyError(f"Unknown provider '{provider}' in cost table")
    model_rows = table[provider]
    if model not in model_rows:
        raise KeyError(f"Unknown model '{model}' for provider '{provider}'")
    row = model_rows[model]
    mode: Mode = row["mode"]

    fx_rate = float(table["fx"]["usd_eur"])
    method = f"table-v{table['table_version']}"

    if mode == "per-token-usage":
        breakdown_usd = _per_token_usage(row, usage, tier)
    elif mode == "per-image":
        breakdown_usd = _per_image(row, tier, image_format)
    elif mode == "per-token":
        breakdown_usd = _per_token(row, usage, tier)
    else:
        raise ValueError(f"Unknown cost mode '{mode}' for {provider}/{model}")

    source_amount = sum(breakdown_usd.values())
    amount_eur = round(source_amount * fx_rate, 6)

    return CostResult(
        amount_eur=amount_eur,
        source_amount=round(source_amount, 6),
        source_currency="USD",
        fx_rate=fx_rate,
        method=method,
        tier=tier,
        breakdown_usd={k: round(v, 6) for k, v in breakdown_usd.items()},
    )


def _per_token_usage(row: dict[str, Any], usage: Usage | None, tier: Tier) -> dict[str, float]:
    if usage is None:
        raise ValueError("per-token-usage mode requires a Usage object")

    rate_in = float(row.get("usd_per_1m_input_tokens", 0.0)) / 1_000_000
    rate_cached_in = float(
        row.get("usd_per_1m_cached_input_tokens", row.get("usd_per_1m_input_tokens", 0.0))
    ) / 1_000_000
    rate_out = float(row.get("usd_per_1m_output_tokens", 0.0)) / 1_000_000

    cached = usage.cached_input_tokens or 0
    fresh_in = max(0, (usage.input_tokens or 0) - cached)
    out = usage.output_tokens or 0

    breakdown = {
        "input": fresh_in * rate_in,
        "cached_input": cached * rate_cached_in,
        "output": out * rate_out,
    }
    return _apply_tier_discount(breakdown, row, tier)


def _per_image(row: dict[str, Any], tier: Tier, image_format: Literal["raster", "vector"]) -> dict[str, float]:
    # Recraft splits raster vs vector
    if "usd_raster" in row or "usd_vector" in row:
        key = "usd_vector" if image_format == "vector" else "usd_raster"
        unit = float(row[key])
    elif tier == "batch" and "usd_batch" in row:
        return {"per_image": float(row["usd_batch"])}
    elif row.get("usd") is None:
        raise ValueError("Model row missing USD price — populate the cost table before billing")
    else:
        unit = float(row["usd"])
    breakdown = {"per_image": unit}
    return _apply_tier_discount(breakdown, row, tier)


def _per_token(row: dict[str, Any], usage: Usage | None, tier: Tier) -> dict[str, float]:
    # For text models priced per 1M tokens (same shape as per-token-usage, but no cached tier).
    return _per_token_usage(row, usage, tier)


def _apply_tier_discount(breakdown: dict[str, float], row: dict[str, Any], tier: Tier) -> dict[str, float]:
    if tier == "batch":
        discount = float(row.get("batch_discount", 0.0))
        if discount > 0:
            return {k: v * (1 - discount) for k, v in breakdown.items()}
    return breakdown
