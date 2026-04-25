"""Tests for brand-slug normalisation (CDI-1041 cross-skill alignment)."""

from __future__ import annotations

import pytest

from mcp_bildsprache.brands import (
    CANONICAL_BRANDS,
    is_known_brand,
    normalize_brand,
)
from mcp_bildsprache.presets import get_preset
from mcp_bildsprache.slugs import _resolve_brand_prefix


class TestNormalizeBrand:
    @pytest.mark.parametrize(
        "alias,canonical",
        [
            # Bildsprache historical
            ("@casey.berlin", "casey-berlin"),
            ("casey.berlin", "casey-berlin"),
            ("@cdit", "cdit-works"),
            ("cdit", "cdit-works"),
            ("@cdit-works", "cdit-works"),
            ("@cdit.works", "cdit-works"),
            ("cdit-works.de", "cdit-works"),
            ("cdit.works", "cdit-works"),
            ("@storykeep", "storykeep"),
            ("@nah", "nah"),
            ("@yorizon", "yorizon"),
            # Underscored
            ("casey_berlin", "casey-berlin"),
            ("cdit_works", "cdit-works"),
            # Already canonical
            ("casey-berlin", "casey-berlin"),
            ("cdit-works", "cdit-works"),
            ("storykeep", "storykeep"),
            ("nah", "nah"),
            ("yorizon", "yorizon"),
        ],
    )
    def test_known_aliases(self, alias: str, canonical: str) -> None:
        assert normalize_brand(alias) == canonical

    def test_none_passes_through(self) -> None:
        assert normalize_brand(None) is None

    def test_empty_passes_through(self) -> None:
        assert normalize_brand("") == ""

    def test_unknown_passes_through_unchanged(self) -> None:
        assert normalize_brand("totally-unknown") == "totally-unknown"

    def test_whitespace_stripped(self) -> None:
        assert normalize_brand("  @cdit  ") == "cdit-works"


class TestIsKnownBrand:
    def test_canonical_known(self) -> None:
        for c in CANONICAL_BRANDS:
            assert is_known_brand(c) is True

    def test_aliases_known(self) -> None:
        assert is_known_brand("@cdit")
        assert is_known_brand("@casey.berlin")
        assert is_known_brand("cdit-works.de")

    def test_unknown_unknown(self) -> None:
        assert is_known_brand("nope") is False

    def test_falsy_unknown(self) -> None:
        assert is_known_brand(None) is False
        assert is_known_brand("") is False


class TestGetPresetAcceptsAllVariants:
    """Every variant of the same brand should yield the same preset."""

    @pytest.mark.parametrize(
        "variants",
        [
            ("casey-berlin", "@casey.berlin", "casey.berlin", "casey_berlin"),
            ("cdit-works", "@cdit", "cdit", "@cdit-works", "cdit-works.de", "cdit_works"),
            ("storykeep", "@storykeep"),
            ("nah", "@nah"),
            ("yorizon", "@yorizon"),
        ],
    )
    def test_variants_yield_same_preset(self, variants: tuple[str, ...]) -> None:
        presets = {get_preset(v) for v in variants}
        assert len(presets) == 1, f"{variants} yielded {len(presets)} different presets"


class TestResolveBrandPrefixAcceptsAllVariants:
    """Every variant of the same brand should yield the same URL prefix."""

    @pytest.mark.parametrize(
        "variants,expected_prefix",
        [
            (("casey-berlin", "@casey.berlin", "casey.berlin"), "casey-berlin"),
            (("cdit-works", "@cdit", "cdit", "@cdit-works", "cdit-works.de"), "cdit"),
            (("storykeep", "@storykeep"), "storykeep"),
            (("nah", "@nah"), "nah"),
            (("yorizon", "@yorizon"), "yorizon"),
        ],
    )
    def test_variants_yield_same_prefix(
        self, variants: tuple[str, ...], expected_prefix: str
    ) -> None:
        for v in variants:
            assert _resolve_brand_prefix(v) == expected_prefix

    def test_unknown_falls_through_to_gen(self) -> None:
        assert _resolve_brand_prefix("totally-unknown") == "gen"

    def test_none_returns_gen(self) -> None:
        assert _resolve_brand_prefix(None) == "gen"
