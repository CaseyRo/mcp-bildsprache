"""Tests for CDI-1014 §3 attribution builder.

Covers:
- build_attribution produces schema-compliant payloads for each provider path
- cost block reconciles with breakdown_usd
- Gemini SynthID default honoured
- Legacy cost_estimate derivation
- Fallback to legacy cost-string path when cost table lookup fails
"""

from __future__ import annotations

import pytest

from mcp_bildsprache.attribution import (
    SCHEMA_VERSION,
    build_attribution,
    format_legacy_cost_estimate,
)
from mcp_bildsprache.types import ProviderResult


def _bfl_result() -> ProviderResult:
    return ProviderResult(
        image_data=b"fake-bytes",
        mime_type="image/webp",
        model="flux-2-pro",
        cost_estimate="$0.03",
    )


def _gemini_result() -> ProviderResult:
    return ProviderResult(
        image_data=b"fake-bytes",
        mime_type="image/webp",
        model="gemini-3.1-flash-image-preview",
        cost_estimate="$0.067",
        usage={"input_tokens": 180, "output_tokens": 1120},
    )


def _openai_result() -> ProviderResult:
    return ProviderResult(
        image_data=b"fake-bytes",
        mime_type="image/webp",
        model="gpt-image-2",
        cost_estimate="$0.036",
        usage={
            "input_tokens": 412,
            "output_tokens": 1120,
            "input_tokens_details": {
                "text_tokens": 112,
                "image_tokens": 300,
                "cached_tokens": 0,
            },
        },
        revised_prompt="A reflective early-morning Kreuzberg scene",
        model_version="gpt-image-2-2026-04-21",
    )


def _recraft_result() -> ProviderResult:
    return ProviderResult(
        image_data=b"fake-bytes",
        mime_type="image/webp",
        model="recraftv4",
        cost_estimate="$0.04",
    )


class TestBuildAttributionShape:
    def test_required_fields_present(self) -> None:
        p = build_attribution(
            provider_result=_bfl_result(),
            prompt_anchor="A quiet Kreuzberg courtyard",
        )
        for key in (
            "schema_version",
            "provider",
            "model",
            "model_version",
            "kind",
            "tokens",
            "cost",
            "prompt_anchor",
            "prompt_hash",
            "sources",
            "generated_at",
            "provenance",
        ):
            assert key in p, f"missing {key}"
        assert p["schema_version"] == SCHEMA_VERSION
        assert p["kind"] == "image"

    def test_prompt_hash_is_sha256_prefixed(self) -> None:
        p = build_attribution(
            provider_result=_bfl_result(),
            prompt_anchor="hello",
        )
        assert p["prompt_hash"].startswith("sha256:")
        assert len(p["prompt_hash"]) == len("sha256:") + 64

    def test_effective_prompt_changes_hash(self) -> None:
        pa = build_attribution(
            provider_result=_bfl_result(),
            prompt_anchor="author intent",
        )
        pb = build_attribution(
            provider_result=_bfl_result(),
            prompt_anchor="author intent",
            effective_prompt="author intent — with brand preset",
        )
        assert pa["prompt_hash"] != pb["prompt_hash"]
        # The anchor (what consumers render) stays author-visible regardless.
        assert pa["prompt_anchor"] == pb["prompt_anchor"] == "author intent"

    def test_generated_at_is_utc_zulu(self) -> None:
        p = build_attribution(
            provider_result=_bfl_result(),
            prompt_anchor="x",
        )
        assert p["generated_at"].endswith("Z")
        assert "T" in p["generated_at"]


class TestProviderInference:
    def test_bfl(self) -> None:
        p = build_attribution(provider_result=_bfl_result(), prompt_anchor="x")
        assert p["provider"] == "bfl"

    def test_gemini(self) -> None:
        p = build_attribution(provider_result=_gemini_result(), prompt_anchor="x")
        assert p["provider"] == "gemini"

    def test_openai(self) -> None:
        p = build_attribution(provider_result=_openai_result(), prompt_anchor="x")
        assert p["provider"] == "openai"

    def test_recraft(self) -> None:
        p = build_attribution(provider_result=_recraft_result(), prompt_anchor="x")
        assert p["provider"] == "recraft"


class TestTokensBlock:
    def test_per_image_provider_tokens_are_null(self) -> None:
        p = build_attribution(provider_result=_bfl_result(), prompt_anchor="x")
        assert p["tokens"]["input"] is None
        assert p["tokens"]["output"] is None
        assert p["tokens"]["units"] == "tokens"

    def test_openai_includes_full_breakdown(self) -> None:
        p = build_attribution(provider_result=_openai_result(), prompt_anchor="x")
        b = p["tokens"]["breakdown"]
        assert b["text_input"] == 112
        assert b["image_input"] == 300
        assert b["cached_input"] == 0
        assert b["image_output"] == 1120
        assert p["tokens"]["input"] == 412
        assert p["tokens"]["output"] == 1120


class TestCostBlock:
    def test_bfl_per_image_cost_from_table(self) -> None:
        p = build_attribution(provider_result=_bfl_result(), prompt_anchor="x")
        cost = p["cost"]
        # flux-2-pro = $0.03 in provider_costs.v1.yaml
        assert cost["source_amount"] == pytest.approx(0.03, rel=1e-6)
        assert cost["source_currency"] == "USD"
        assert cost["fx_rate"] == pytest.approx(0.92, rel=1e-9)
        assert cost["amount_eur"] == pytest.approx(0.03 * 0.92, rel=1e-5)
        assert cost["method"] == "table-v1"
        assert cost["tier"] == "standard"

    def test_openai_cost_from_usage_block(self) -> None:
        p = build_attribution(provider_result=_openai_result(), prompt_anchor="x")
        cost = p["cost"]
        # 412 input tokens @ $8/1M = $0.003296
        # 1120 output tokens @ $30/1M = $0.0336
        assert cost["breakdown_usd"]["input"] == pytest.approx(0.003296, rel=1e-5)
        assert cost["breakdown_usd"]["output"] == pytest.approx(0.0336, rel=1e-5)
        assert cost["breakdown_usd"]["cached_input"] == 0.0
        total_usd = sum(cost["breakdown_usd"].values())
        assert cost["source_amount"] == pytest.approx(total_usd, rel=1e-6)

    def test_recraft_raster_default(self) -> None:
        p = build_attribution(provider_result=_recraft_result(), prompt_anchor="x")
        # recraftv4 raster = $0.04
        assert p["cost"]["source_amount"] == pytest.approx(0.04, rel=1e-6)

    def test_recraft_vector_path(self) -> None:
        p = build_attribution(
            provider_result=_recraft_result(),
            prompt_anchor="x",
            image_format="vector",
        )
        # recraftv4 vector = $0.08
        assert p["cost"]["source_amount"] == pytest.approx(0.08, rel=1e-6)

    def test_batch_tier_applies_discount(self) -> None:
        standard = build_attribution(
            provider_result=_openai_result(), prompt_anchor="x", tier="standard"
        )
        batch = build_attribution(
            provider_result=_openai_result(), prompt_anchor="x", tier="batch"
        )
        assert batch["cost"]["tier"] == "batch"
        assert batch["cost"]["source_amount"] == pytest.approx(
            standard["cost"]["source_amount"] * 0.5, rel=1e-5
        )

    def test_unknown_model_falls_back_to_legacy_string(self) -> None:
        # A model id not in the table triggers the legacy cost-string fallback.
        pr = ProviderResult(
            image_data=b"x",
            mime_type="image/webp",
            model="flux-unknown-future",
            cost_estimate="$0.12",
        )
        p = build_attribution(provider_result=pr, prompt_anchor="x")
        assert p["cost"]["method"] == "legacy-string"
        assert p["cost"]["source_amount"] == pytest.approx(0.12, rel=1e-6)


class TestProvenance:
    def test_gemini_defaults_to_synthid_true(self) -> None:
        p = build_attribution(provider_result=_gemini_result(), prompt_anchor="x")
        assert p["provenance"]["synthid"] is True
        assert p["provenance"]["c2pa"] is False

    def test_non_gemini_synthid_false(self) -> None:
        for fn in (_bfl_result, _openai_result, _recraft_result):
            p = build_attribution(provider_result=fn(), prompt_anchor="x")
            assert p["provenance"]["synthid"] is False

    def test_explicit_flags_override_default(self) -> None:
        pr = ProviderResult(
            image_data=b"x",
            mime_type="image/webp",
            model="flux-2-pro",
            cost_estimate="$0.03",
            provenance_flags={"synthid": True, "c2pa": True, "exif_signature": "custom-v2"},
        )
        p = build_attribution(provider_result=pr, prompt_anchor="x")
        assert p["provenance"]["synthid"] is True
        assert p["provenance"]["c2pa"] is True
        assert p["provenance"]["exif_signature"] == "custom-v2"


class TestRevisedPrompt:
    def test_openai_revised_prompt_included(self) -> None:
        p = build_attribution(provider_result=_openai_result(), prompt_anchor="original")
        assert p["revised_prompt"] == "A reflective early-morning Kreuzberg scene"
        assert p["prompt_anchor"] == "original"

    def test_bfl_no_revised_prompt_field(self) -> None:
        p = build_attribution(provider_result=_bfl_result(), prompt_anchor="x")
        assert "revised_prompt" not in p


class TestSources:
    def test_empty_when_no_sources(self) -> None:
        p = build_attribution(provider_result=_bfl_result(), prompt_anchor="x")
        assert p["sources"] == []

    def test_source_array_preserved(self) -> None:
        srcs = [
            {"type": "stolperstein", "id": "SK-0481", "title": "A note"},
            {"type": "url", "href": "https://example.com"},
        ]
        p = build_attribution(
            provider_result=_bfl_result(), prompt_anchor="x", sources=srcs
        )
        assert p["sources"] == srcs


class TestParams:
    def test_params_included_when_provided(self) -> None:
        p = build_attribution(
            provider_result=_openai_result(),
            prompt_anchor="x",
            params={"size": "1024x1024", "quality": "medium"},
        )
        assert p["params"]["size"] == "1024x1024"
        assert p["params"]["quality"] == "medium"

    def test_params_omitted_when_none(self) -> None:
        p = build_attribution(provider_result=_openai_result(), prompt_anchor="x")
        assert "params" not in p


class TestLegacyCostEstimate:
    def test_formats_eur_amount(self) -> None:
        payload = build_attribution(provider_result=_bfl_result(), prompt_anchor="x")
        legacy = format_legacy_cost_estimate(payload)
        # 0.03 * 0.92 = 0.0276
        assert legacy.startswith("€")
        assert "0.0276" in legacy


class TestModelVersion:
    def test_openai_uses_explicit_model_version(self) -> None:
        p = build_attribution(provider_result=_openai_result(), prompt_anchor="x")
        assert p["model_version"] == "gpt-image-2-2026-04-21"

    def test_bfl_falls_back_to_model_id(self) -> None:
        p = build_attribution(provider_result=_bfl_result(), prompt_anchor="x")
        assert p["model_version"] == "flux-2-pro"
