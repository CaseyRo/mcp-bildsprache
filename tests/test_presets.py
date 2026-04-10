"""Tests for brand presets, dimensions, and model routing."""

from __future__ import annotations

import pytest

from mcp_bildsprache.presets import (
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


class TestPlatformSizes:
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
