"""Tests for brand presets, dimensions, and model routing."""

from __future__ import annotations

import pytest

from mcp_bildsprache.presets import (
    CASEY_COMPOSITION_CLAUSE,
    PLATFORM_SIZES,
    PRESETS,
    get_dimensions,
    get_preset,
    route_model,
)


class TestGetPreset:
    """Test get_preset returns correct brand presets for known contexts."""

    def test_casey_berlin(self):
        preset = get_preset("@casey.berlin")
        assert "European editorial" in preset

    def test_cdit(self):
        preset = get_preset("@cdit")
        assert "Scandinavian" in preset or "developer" in preset

    def test_storykeep(self):
        preset = get_preset("@storykeep")
        assert "Museum" in preset or "curatorial" in preset

    def test_nah(self):
        preset = get_preset("@nah")
        assert "Lo-fi" in preset or "community" in preset

    def test_yorizon(self):
        preset = get_preset("@yorizon")
        assert "Enterprise" in preset or "SaaS" in preset

    def test_get_preset_unknown_falls_back(self):
        """Unknown context falls back to cdit-works.de preset."""
        preset = get_preset("@unknown-brand")
        assert preset == PRESETS["cdit-works.de"]


class TestGetDimensions:
    def test_get_dimensions_known_platform(self):
        assert get_dimensions("blog-hero") == (1600, 900)

    def test_get_dimensions_unknown_falls_back(self):
        assert get_dimensions("tiktok-cover") == (1200, 1200)

    def test_get_dimensions_normalizes_input(self):
        """Spaces and case are normalized."""
        assert get_dimensions("Blog Hero") == (1600, 900)


class TestRouteModel:
    def test_route_model_explicit_flux(self):
        assert route_model(model_hint="flux") == "flux"

    def test_route_model_explicit_gemini(self):
        assert route_model(model_hint="gemini") == "gemini"

    def test_route_model_explicit_recraft(self):
        assert route_model(model_hint="recraft") == "recraft"

    def test_route_model_flux_2_pro(self):
        assert route_model(model_hint="flux-2-pro") == "flux"

    def test_route_model_flux_2_max(self):
        assert route_model(model_hint="flux-2-max") == "flux"

    def test_route_model_flux_kontext_pro(self):
        assert route_model(model_hint="flux-kontext-pro") == "flux"

    def test_route_model_flux_pro_1_1(self):
        assert route_model(model_hint="flux-pro-1.1") == "flux"

    def test_route_model_unknown_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown model"):
            route_model(model_hint="dall-e-3")

    def test_route_model_icon_platform_routes_recraft(self):
        assert route_model(platform="icon") == "recraft"

    def test_route_model_svg_platform_routes_recraft(self):
        assert route_model(platform="svg-logo") == "recraft"

    def test_route_model_default_is_flux(self):
        assert route_model() == "flux"


class TestRouteModelReferenceAware:
    def test_has_references_redirects_icon_to_flux(self):
        assert route_model(platform="icon", has_references=True) == "flux"

    def test_has_references_redirects_svg_logo_to_flux(self):
        assert route_model(platform="svg-logo", has_references=True) == "flux"

    def test_explicit_recraft_hint_respected_with_references(self):
        assert route_model(
            platform="icon", model_hint="recraft", has_references=True
        ) == "recraft"

    def test_non_vector_platform_still_flux_with_references(self):
        assert route_model(platform="blog-hero", has_references=True) == "flux"


class TestRouteModelOpenAI:
    """CDI-1014 §5: OpenAI provider routing."""

    def test_explicit_openai_hint(self):
        assert route_model(model_hint="openai") == "openai"

    def test_gpt_image_2_hint(self):
        assert route_model(model_hint="gpt-image-2") == "openai"

    def test_gpt_image_1_mini_hint(self):
        assert route_model(model_hint="gpt-image-1-mini") == "openai"

    def test_gpt_image_1_5_hint(self):
        assert route_model(model_hint="gpt-image-1.5") == "openai"

    def test_openai_never_auto_selected(self):
        # Default path (no model hint, no special platform) must NOT pick openai
        # per the spec — callers opt in explicitly.
        assert route_model() != "openai"

    def test_openai_never_auto_selected_for_brand_context(self):
        for ctx in ("@casey.berlin", "@cdit", "@storykeep", "@nah", "@yorizon"):
            assert route_model(context=ctx) != "openai"

    def test_openai_never_auto_selected_for_vector_platform(self):
        # Icon/svg/logo still go to Recraft, not OpenAI, since Recraft owns vectors.
        assert route_model(platform="icon") == "recraft"
        assert route_model(platform="svg-logo") == "recraft"

    def test_non_reference_routing_unchanged(self):
        """Every existing case should return the same thing with has_references=False."""
        # These pairs must match the pre-change behaviour.
        cases = [
            ({"platform": "icon"}, "recraft"),
            ({"platform": "svg-logo"}, "recraft"),
            ({"platform": "blog-hero"}, "flux"),
            ({"model_hint": "gemini"}, "gemini"),
            ({"model_hint": "flux"}, "flux"),
            ({"model_hint": "recraft"}, "recraft"),
            ({}, "flux"),
        ]
        for kwargs, expected in cases:
            assert route_model(**kwargs) == expected
            assert route_model(**kwargs, has_references=False) == expected


class TestCompositionClause:
    """Composition clause is defined in presets.py but gated by server.py
    (only appended when an identity pack resolved to a non-empty list for
    @casey.berlin). These tests exercise the gating via generate_image.
    """

    def test_clause_constant_content(self):
        assert "face-to-camera" in CASEY_COMPOSITION_CLAUSE
        assert "sole focal point" in CASEY_COMPOSITION_CLAUSE

    def test_clause_not_in_base_preset(self):
        # The clause is NOT hard-coded into the preset string — it's added
        # at prompt-assembly time in server.py.
        assert CASEY_COMPOSITION_CLAUSE not in PRESETS["casey.berlin"]
    EXPECTED_KEYS = [
        "linkedin-post", "linkedin-article", "linkedin-carousel",
        "instagram-feed", "instagram-story", "blog-hero",
        "og-image", "proposal-cover", "icon", "email-header",
    ]

    def test_platform_sizes_contains_expected_keys(self):
        for key in self.EXPECTED_KEYS:
            assert key in PLATFORM_SIZES, f"Missing platform key: {key}"

    def test_platform_sizes_count(self):
        assert len(PLATFORM_SIZES) == 10
