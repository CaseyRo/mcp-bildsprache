"""Typed output models for the Bildsprache MCP tools.

These Pydantic models exist so FastMCP can advertise an ``output_schema``
for each tool — giving clients a machine-readable, introspectable contract
for the response shape (hosted URL, model, cost, attribution, ...).

Backward-compatibility note: every model sets ``extra="allow"`` and the
tool bodies continue to return plain ``dict`` values. FastMCP serializes
those dicts against the model schema without re-validating, so:

* the documented top-level fields are published in the schema, and
* conditional / legacy extras the tools already emit (``error`` blocks,
  ``fallback_used`` / ``intended_provider`` / ``fallback_reason``,
  ``raw_url`` / ``raw_mime_type``, ``identity_pack_loaded``, ...) still
  pass through verbatim via ``additionalProperties: true``.

This keeps the existing live clients and the manually-synced Cloudflare
portal working — no top-level field is renamed or removed — while
upgrading the tools from "untyped dict" to "typed structured output".
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class GenerateImageResult(BaseModel):
    """Structured result of :func:`generate_image`.

    ``hosted_url`` is the canonical artifact (processed WebP on
    ``img.cdit-works.de``). ``ai_attribution`` is the JSON-Schema-validated
    provenance + EUR-cost block. Optional fields appear only in the relevant
    cases (``raw_url`` when ``raw=True``; the ``fallback_*`` trio when a
    cross-provider fallback fired; ``error`` when a provider hint was
    rejected before any generation happened).
    """

    model_config = ConfigDict(extra="allow")

    hosted_url: Optional[str] = Field(
        default=None,
        description="Public URL of the processed WebP image, or omitted when an error block is returned.",
    )
    model: Optional[str] = Field(
        default=None, description="Provider model id that produced the image (e.g. 'gpt-image-2')."
    )
    cost_estimate: Optional[str] = Field(
        default=None, description="Human display cost derived from ai_attribution (e.g. '€0.0490')."
    )
    brand_context: Optional[str] = Field(
        default=None, description="Brand context applied, if any (e.g. 'casey')."
    )
    platform: Optional[str] = Field(default=None, description="Target platform, if supplied.")
    dimensions: Optional[str] = Field(default=None, description="Final image dimensions as 'WxH'.")
    response_mode: Optional[str] = Field(
        default=None, description="Always 'url' on success (the image is hosted, not inlined)."
    )
    ai_attribution: Optional[dict[str, Any]] = Field(
        default=None, description="ai_attribution v1 payload: provider, cost (EUR), provenance, prompt_hash."
    )
    raw_url: Optional[str] = Field(
        default=None, description="URL of the unprocessed provider image (only when raw=True)."
    )
    raw_mime_type: Optional[str] = Field(
        default=None, description="MIME type of the raw image (only when raw=True)."
    )
    fallback_used: Optional[bool] = Field(
        default=None, description="True when a cross-provider fallback produced the image."
    )
    intended_provider: Optional[str] = Field(
        default=None, description="Provider originally selected, when a fallback fired."
    )
    fallback_reason: Optional[str] = Field(
        default=None, description="Why the fallback fired (e.g. 'provider_error')."
    )
    error: Optional[dict[str, Any]] = Field(
        default=None,
        description="Structured error block (e.g. PROVIDER_TEMPORARILY_DISABLED) when the call was rejected before generation.",
    )
    cancelled: Optional[bool] = Field(
        default=None,
        description="True when the user declined/cancelled the cost-confirmation prompt; no provider call was made and no artifact was written.",
    )
    estimated_cost_eur: Optional[float] = Field(
        default=None,
        description="Estimated EUR cost surfaced in the cost-confirmation prompt (only present when the user cancelled).",
    )


class GenerateDiagramResult(BaseModel):
    """Structured result of :func:`generate_diagram`.

    Same storage pipeline as :class:`GenerateImageResult`; diagrams always
    carry the casey brand context and add ``format`` + ``register``.
    """

    # ``populate_by_name`` lets the `register_` field accept/emit the wire
    # name ``register`` (which shadows BaseModel.register, so it can't be a
    # bare attribute name). The tools return plain dicts keyed ``register``.
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    hosted_url: Optional[str] = Field(
        default=None, description="Public URL of the processed diagram WebP, or omitted on error."
    )
    model: Optional[str] = Field(default=None, description="Provider model id that produced the diagram.")
    cost_estimate: Optional[str] = Field(default=None, description="Human display cost derived from ai_attribution.")
    brand_context: Optional[str] = Field(default=None, description="Always 'casey' for diagrams.")
    register_: Optional[str] = Field(
        default=None, alias="register", description="'personal' or 'professional'."
    )
    format: Optional[str] = Field(default=None, description="Diagram format: 'flow', 'sequence', or 'state'.")
    dimensions: Optional[str] = Field(default=None, description="Final diagram dimensions as 'WxH'.")
    response_mode: Optional[str] = Field(default=None, description="Always 'url' on success.")
    ai_attribution: Optional[dict[str, Any]] = Field(
        default=None, description="ai_attribution v1 payload for the diagram."
    )
    fallback_used: Optional[bool] = Field(default=None, description="True when a cross-provider fallback fired.")
    intended_provider: Optional[str] = Field(default=None, description="Provider originally selected, on fallback.")
    fallback_reason: Optional[str] = Field(default=None, description="Why the fallback fired.")
    error: Optional[dict[str, Any]] = Field(
        default=None,
        description="Structured error block (INVALID_INPUT, MERMAID_PARSE_ERROR, MERMAID_FORMAT_MISMATCH, INVALID_DIMENSIONS, INVALID_MODEL_HINT, PROVIDER_TEMPORARILY_DISABLED).",
    )
    cancelled: Optional[bool] = Field(
        default=None,
        description="True when the user declined/cancelled the cost-confirmation prompt; no provider call was made and no artifact was written.",
    )
    estimated_cost_eur: Optional[float] = Field(
        default=None,
        description="Estimated EUR cost surfaced in the cost-confirmation prompt (only present when the user cancelled).",
    )


class GeneratePromptResult(BaseModel):
    """Structured result of :func:`generate_prompt` (no provider call)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    engineered_prompt: Optional[str] = Field(
        default=None, description="The brand-injected prompt that would be sent to the provider."
    )
    model: Optional[str] = Field(default=None, description="Provider key the prompt would route to ('openai'/'gemini').")
    dimensions: Optional[str] = Field(default=None, description="Recommended dimensions as 'WxH'.")
    brand_context: Optional[str] = Field(default=None, description="Brand context applied, if any.")
    register_: Optional[str] = Field(
        default=None, alias="register", description="Register applied, if any."
    )
    platform: Optional[str] = Field(default=None, description="Platform supplied, if any.")
    error: Optional[dict[str, Any]] = Field(
        default=None, description="Structured error block when a disabled provider was hinted."
    )


class GetImageResult(BaseModel):
    """Structured result of :func:`get_image_result` (CDI-1266 async poll).

    ``status`` is the discriminant:

    * ``"done"``      — the render completed; the full generate_image/
      generate_diagram result is spread in at the top level (``hosted_url``,
      ``model``, ``cost_estimate``, ``ai_attribution``, ...), recoverable from the
      in-process registry or — across restarts — the durable CDI-1264 ledger.
    * ``"pending"``   — still rendering; poll again (optionally with
      ``wait_seconds`` to long-poll).
    * ``"error"``     — the render failed; ``error`` carries the message.
    * ``"not_found"`` — the ``job_id`` is unknown to both the registry and the
      ledger (never dispatched, or evicted before completion with no ledger line).

    ``extra="allow"`` lets the spread-in success fields pass through verbatim.
    """

    model_config = ConfigDict(extra="allow")

    job_id: str = Field(description="The polled job id (== the generation request_id).")
    status: str = Field(description="One of: pending | done | error | not_found.")
    hosted_url: Optional[str] = Field(
        default=None, description="Public WebP URL — present when status == 'done'."
    )
    model: Optional[str] = Field(default=None, description="Provider model id, when known.")
    brand_context: Optional[str] = Field(default=None, description="Brand context, when known.")
    dimensions: Optional[str] = Field(default=None, description="Image dimensions as 'WxH', when known.")
    latency_ms: Optional[int] = Field(
        default=None, description="Dispatch→completion latency in ms (done/error)."
    )
    error: Optional[Any] = Field(
        default=None, description="Error message string when status == 'error'."
    )
    error_category: Optional[str] = Field(
        default=None, description="Exception class name when status == 'error'."
    )
    source: Optional[str] = Field(
        default=None,
        description="Where the result was resolved from: 'registry' | 'ledger' (durable fallback).",
    )
    ai_attribution: Optional[dict[str, Any]] = Field(
        default=None, description="ai_attribution payload, spread in when status == 'done'."
    )


class RecentGeneration(BaseModel):
    """One previously generated artifact surfaced by
    :func:`list_recent_generations`.

    Sourced from the on-disk sidecar index (the same data the gallery
    serves), so a render that completed server-side but whose
    streamable-HTTP response was lost to a session timeout is still
    recoverable here by its ``hosted_url``.
    """

    model_config = ConfigDict(extra="allow")

    hosted_url: str = Field(description="Public URL of the processed WebP image.")
    path: str = Field(description="Storage path relative to the image data dir (POSIX separators).")
    brand: str = Field(description="Top-level brand directory (e.g. 'casey', 'yorizon').")
    prompt: str = Field(description="Stored prompt text for the generation (may be empty for legacy sidecars).")
    model: str = Field(default="", description="Provider model id that produced the image.")
    cost_estimate: str = Field(default="", description="Stored cost estimate string for the generation.")
    width: int = Field(default=0, description="Image width in pixels (0 when unknown).")
    height: int = Field(default=0, description="Image height in pixels (0 when unknown).")
    platform: Optional[str] = Field(default=None, description="Target platform stored at generation time, if any.")
    file_size: int = Field(default=0, description="File size of the WebP in bytes (0 when unknown).")
    created_at: str = Field(description="ISO-8601 timestamp the artifact was generated (or file mtime fallback).")


class RecentGenerationsResult(BaseModel):
    """Structured result of :func:`list_recent_generations`.

    ``generations`` is the most-recent-first page of artifacts. ``total``
    is the count after the optional brand filter but before pagination, so
    a caller can tell whether more remain beyond ``limit``/``offset``. An
    empty ``generations`` list (with ``total == 0``) is the clean, non-error
    response for "nothing matched" — never an exception.
    """

    model_config = ConfigDict(extra="allow")

    generations: list[RecentGeneration] = Field(
        default_factory=list, description="Matching artifacts, newest first."
    )
    total: int = Field(default=0, description="Total matches after filtering, before limit/offset.")
    returned: int = Field(default=0, description="Number of entries in this page (len of generations).")
    limit: int = Field(default=0, description="Effective page size applied (after clamping).")
    offset: int = Field(default=0, description="Offset applied into the filtered set.")
    brand: Optional[str] = Field(default=None, description="Brand filter applied, if any.")


class ModelStat(BaseModel):
    """Per-model aggregate row in :func:`generation_stats`."""

    model_config = ConfigDict(extra="allow")

    model: str = Field(description="Model id the attempts ran against (or 'unknown').")
    attempts: int = Field(default=0, description="Total generation attempts for this model in-window.")
    successes: int = Field(default=0, description="Attempts whose outcome was 'success'.")
    failures: int = Field(default=0, description="Attempts whose outcome was not 'success'.")
    success_rate: float = Field(default=0.0, description="successes / attempts, 0.0-1.0 (0 when no attempts).")
    success_pct: float = Field(default=0.0, description="success_rate as a percentage (0-100).")
    outcomes: dict[str, int] = Field(
        default_factory=dict,
        description="Outcome -> count breakdown (success, provider_error, timeout, teardown_closed_stream, other).",
    )


class GenerationStatsResult(BaseModel):
    """Structured result of :func:`generation_stats` (CDI-1264).

    Reads the durable append-only outcome ledger and reports attempts /
    successes / failures and success% grouped by model over a time window.
    Reads only local JSONL — no provider call, no cost. An empty ledger (or a
    window with no records) returns clean zeros, never an error.
    """

    model_config = ConfigDict(extra="allow")

    window: dict[str, Any] = Field(
        default_factory=dict,
        description="Window applied: {since (ISO8601|null), days (int|null), limit (int|null)}.",
    )
    totals: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Aggregate across all models: attempts, successes, failures, "
            "success_rate, success_pct, plus delivered vs teardown_closed_stream "
            "(succeeded-but-undelivered) split."
        ),
    )
    by_model: list[ModelStat] = Field(
        default_factory=list, description="Per-model stats, first-seen order."
    )


class ProviderInfo(BaseModel):
    """One active provider entry advertised by :func:`list_models`."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Provider key ('openai' / 'gemini').")
    name: str = Field(description="Human-readable provider/model name.")
    models: list[str] = Field(default_factory=list, description="Concrete model ids this provider can run.")
    best_for: Optional[str] = Field(default=None, description="When to prefer this provider.")
    cost: Optional[str] = Field(default=None, description="Rough per-image cost band.")
    status: Optional[str] = Field(default=None, description="Availability status (e.g. 'available').")


class ModelsResult(BaseModel):
    """Structured result of :func:`list_models`."""

    model_config = ConfigDict(extra="allow")

    providers: list[ProviderInfo] = Field(
        default_factory=list, description="Active providers reachable via the dispatcher."
    )
    disabled_providers: list[dict[str, Any]] = Field(
        default_factory=list, description="In-tree but undispatched providers (FLUX/Recraft) with reasons."
    )
    identity_packs: dict[str, bool] = Field(
        default_factory=dict, description="Brand -> whether an identity pack is currently loaded."
    )
    diagram_capable: list[str] = Field(
        default_factory=list, description="Providers usable for generate_diagram."
    )
    diagram_formats: list[str] = Field(
        default_factory=list, description="Supported diagram formats (flow/sequence/state)."
    )


class VisualPresetsResult(BaseModel):
    """Structured result of :func:`get_visual_presets`.

    Two shapes share one model (``extra='allow'`` covers both): the
    single-context shape (``context`` + ``preset`` + ``identity_pack_loaded``)
    and the all-presets shape (``presets`` + ``casey_register_overlays`` +
    ``platforms`` + ``identity_packs``).
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    context: Optional[str] = Field(default=None, description="Brand context requested (single-context shape).")
    register_: Optional[str] = Field(
        default=None, alias="register", description="Register requested (single-context shape)."
    )
    preset: Optional[str] = Field(default=None, description="Resolved preset text (single-context shape).")
    identity_pack_loaded: Optional[bool] = Field(
        default=None, description="Whether an identity pack is loaded for the context (single-context shape)."
    )
    presets: Optional[dict[str, str]] = Field(
        default=None, description="All brand presets (all-presets shape)."
    )
    casey_register_overlays: Optional[dict[str, str]] = Field(
        default=None, description="Casey personal/professional register overlays (all-presets shape)."
    )
    platforms: Optional[dict[str, Any]] = Field(
        default=None, description="Platform -> (width, height) sizing table (all-presets shape)."
    )
    identity_packs: Optional[dict[str, bool]] = Field(
        default=None, description="Brand -> loaded flag (all-presets shape)."
    )
