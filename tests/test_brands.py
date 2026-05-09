"""Tests for brand-slug normalisation (May 2026 brand collapse)."""

from __future__ import annotations

import pytest

from mcp_bildsprache.brands import (
    CANONICAL_BRANDS,
    is_known_brand,
    normalize_brand,
)


class TestNormalizeBrand:
    @pytest.mark.parametrize(
        "alias,canonical",
        [
            # Casey-side legacy forms all collapse to 'casey'.
            ("@casey", "casey"),
            ("@casey.berlin", "casey"),
            ("casey.berlin", "casey"),
            ("casey-berlin", "casey"),
            ("casey_berlin", "casey"),
            ("@cdit", "casey"),
            ("cdit", "casey"),
            ("@cdit-works", "casey"),
            ("@cdit.works", "casey"),
            ("cdit-works", "casey"),
            ("cdit-works.de", "casey"),
            ("cdit.works", "casey"),
            ("cdit_works", "casey"),
            ("@storykeep", "casey"),
            ("storykeep", "casey"),
            ("@nah", "casey"),
            ("nah", "casey"),
            # Yorizon stays separate.
            ("@yorizon", "yorizon"),
            ("yorizon", "yorizon"),
            # Already canonical
            ("casey", "casey"),
        ],
    )
    def test_known_aliases(self, alias: str, canonical: str) -> None:
        assert normalize_brand(alias) == canonical

    def test_none_passes_through(self) -> None:
        assert normalize_brand(None) is None

    def test_unknown_passes_through(self) -> None:
        assert normalize_brand("totally-unknown") == "totally-unknown"

    def test_canonical_brands_are_active_only(self) -> None:
        # May 2026 brand collapse: only casey + yorizon.
        assert set(CANONICAL_BRANDS) == {"casey", "yorizon"}


class TestIsKnownBrand:
    @pytest.mark.parametrize(
        "brand,expected",
        [
            ("casey", True),
            ("yorizon", True),
            # Legacy aliases resolve to active canonicals.
            ("casey-berlin", True),
            ("@casey.berlin", True),
            ("cdit-works", True),
            ("@cdit", True),
            ("storykeep", True),
            ("nah", True),
            # Truly unknown.
            ("totally-unknown", False),
            ("", False),
            (None, False),
        ],
    )
    def test_recognition(self, brand, expected):
        assert is_known_brand(brand) is expected
