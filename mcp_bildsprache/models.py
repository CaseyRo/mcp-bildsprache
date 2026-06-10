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
