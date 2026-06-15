"""Tests for brand presets, dimensions, and model routing (May 2026 collapse)."""

from __future__ import annotations

import pytest

from mcp_bildsprache.presets import (
    ACTIVE_PROVIDERS,
    CASEY_COMPOSITION_CLAUSE,
    CASEY_PALETTE,
    CASEY_REGISTER_OVERLAYS,
    DISABLED_PROVIDERS,
    PLATFORM_SIZES,
    PRESETS,
    get_dimensions,
    get_preset,
    route_model,
)
from mcp_bildsprache.types import ProviderTemporarilyDisabled


class TestGetPreset:
    """Active brands: casey + yorizon. Legacy keys resolve to casey."""

    def test_casey_canonical(self):
        preset = get_preset("casey")
        assert "botanical" in preset.lower() or "paper bone" in preset.lower()

    def test_casey_personal_register(self):
        preset = get_preset("casey", register="personal")
        assert "Register: personal" in preset
        assert "kitchen-table" in preset.lower()

    def test_casey_professional_register(self):
        preset = get_preset("casey", register="professional")
        assert "Register: professional" in preset
        assert "schematic" in preset.lower() or "workshop" in preset.lower()

    def test_legacy_casey_berlin_resolves_to_casey(self):
        preset = get_preset("@casey.berlin")
        assert "paper bone" in preset.lower()

    def test_legacy_cdit_resolves_to_casey(self):
        preset = get_preset("@cdit")
        assert "paper bone" in preset.lower() or "casey" in preset.lower()

    def test_legacy_storykeep_resolves_to_casey(self):
        preset = get_preset("@storykeep")
        assert "casey" in preset.lower() or "paper bone" in preset.lower()

    def test_legacy_nah_resolves_to_casey(self):
        preset = get_preset("@nah")
        assert "casey" in preset.lower() or "paper bone" in preset.lower()

    def test_yorizon_isolated(self):
        preset = get_preset("yorizon")
        assert "Enterprise" in preset or "SaaS" in preset
        # Critical: yorizon MUST NOT positively use casey palette tokens.
        # The yorizon preset mentions them in the negative ("NO casey
        # botanical tokens — no paper bone, ..."), so we check that the
        # casey HEX values aren't present (those are only in the casey
        # preset's positive direction).
        for hex_value in ("#F4EFE3", "#2C4A38", "#1F2E26", "#B8884A", "#C7CFB8"):
            assert hex_value not in preset
        # The positive direction must explicitly forbid casey aesthetic.
        assert "yorizon brand colours only" in preset.lower() or "yorizon brand colors only" in preset.lower()

    def test_get_preset_unknown_falls_back_to_casey_professional(self):
        preset = get_preset("@unknown-brand")
        # Unknown contexts default to casey + professional register.
        assert "Register: professional" in preset


class TestCaseyPalette:
    """The locked botanical palette must be present in any casey preset."""

    def test_palette_keys_complete(self):
        assert set(CASEY_PALETTE.keys()) == {
            "paper_bone",
            "forest_moss",
            "pine_ink",
            "weathered_ochre",
            "soft_moss",
        }

    def test_palette_hex_values(self):
        assert CASEY_PALETTE["paper_bone"]["hex"] == "#F4EFE3"
        assert CASEY_PALETTE["forest_moss"]["hex"] == "#2C4A38"
        assert CASEY_PALETTE["pine_ink"]["hex"] == "#1F2E26"
        assert CASEY_PALETTE["weathered_ochre"]["hex"] == "#B8884A"
        assert CASEY_PALETTE["soft_moss"]["hex"] == "#C7CFB8"

    def test_palette_in_casey_preset(self):
        preset = get_preset("casey")
        for token_data in CASEY_PALETTE.values():
            assert token_data["hex"] in preset

    def test_register_overlays_complete(self):
        assert set(CASEY_REGISTER_OVERLAYS.keys()) == {"personal", "professional"}


class TestGetDimensions:
    def test_get_dimensions_known_platform(self):
        assert get_dimensions("blog-hero") == (1600, 900)

    def test_get_dimensions_unknown_falls_back(self):
        assert get_dimensions("tiktok-cover") == (1200, 1200)

    def test_get_dimensions_normalizes_input(self):
        assert get_dimensions("Blog Hero") == (1600, 900)


class TestRouteModel:
    """Active providers: openai (raster default), gemini (diagram default)."""

    def test_route_model_explicit_openai(self):
        assert route_model(model_hint="openai") == "openai"

    def test_route_model_gpt_image_2(self):
        assert route_model(model_hint="gpt-image-2") == "openai"

    def test_route_model_gpt_image_1_5(self):
        # GPT Image 1.5 (high) — added in the model lineup refresh (CDI-1264).
        assert route_model(model_hint="gpt-image-1.5") == "openai"

    def test_route_model_gpt_image_1_mini(self):
        assert route_model(model_hint="gpt-image-1-mini") == "openai"

    def test_route_model_explicit_gemini(self):
        assert route_model(model_hint="gemini") == "gemini"

    def test_route_model_nano_banana(self):
        assert route_model(model_hint="nano-banana-pro") == "gemini"

    def test_route_model_default_is_openai(self):
        # Per the May 2026 collapse, openai is the default raster path.
        assert route_model() == "openai"

    def test_route_model_default_diagram_is_gemini(self):
        assert route_model(intent="diagram") == "gemini"

    def test_route_model_unknown_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown model"):
            route_model(model_hint="dall-e-3")


class TestRouteModelDisabledProviders:
    """FLUX and Recraft hints raise ProviderTemporarilyDisabled."""

    def test_flux_hint_raises(self):
        with pytest.raises(ProviderTemporarilyDisabled) as exc_info:
            route_model(model_hint="flux")
        assert exc_info.value.provider == "FLUX"
        assert exc_info.value.replacement == "openai"

    def test_flux_2_max_hint_raises(self):
        with pytest.raises(ProviderTemporarilyDisabled):
            route_model(model_hint="flux-2-max")

    def test_flux_kontext_pro_hint_raises(self):
        # flux-kontext-pro was dropped (model lineup refresh, CDI-1264), but a
        # legacy caller could still send the hint — the "flux" prefix must
        # still yield the clean FLUX-disabled message, not a cryptic error.
        with pytest.raises(ProviderTemporarilyDisabled):
            route_model(model_hint="flux-kontext-pro")

    def test_flux_pro_1_1_hint_raises(self):
        with pytest.raises(ProviderTemporarilyDisabled):
            route_model(model_hint="flux-pro-1.1")

    def test_recraft_hint_raises(self):
        with pytest.raises(ProviderTemporarilyDisabled) as exc_info:
            route_model(model_hint="recraft")
        assert exc_info.value.provider == "Recraft"

    def test_disabled_replacement_for_diagram_intent(self):
        with pytest.raises(ProviderTemporarilyDisabled) as exc_info:
            route_model(model_hint="flux", intent="diagram")
        assert exc_info.value.replacement == "gemini"


class TestProviderRosters:
    def test_active_providers(self):
        assert set(ACTIVE_PROVIDERS) == {"openai", "gemini"}

    def test_disabled_providers(self):
        ids = {p["provider"] for p in DISABLED_PROVIDERS}
        assert ids == {"bfl", "recraft"}
        for entry in DISABLED_PROVIDERS:
            assert entry["reason"]


class TestCompositionClause:
    def test_clause_constant_content(self):
        assert "face-to-camera" in CASEY_COMPOSITION_CLAUSE
        assert "sole focal point" in CASEY_COMPOSITION_CLAUSE

    def test_clause_not_in_base_preset(self):
        # The clause is gated by server.py — must NOT be in the static
        # preset string.
        assert CASEY_COMPOSITION_CLAUSE not in PRESETS["casey"]


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
