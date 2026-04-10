"""Tests for image storage and slug generation."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_bildsprache.slugs import make_collision_suffix, make_slug
from mcp_bildsprache.storage import StorageError, store_image, store_raw_image


class TestSlugGeneration:
    def test_branded_slug(self):
        prefix, filename = make_slug(
            "morning walk through Kreuzberg", 1200, 630, brand_context="@casey.berlin"
        )
        assert prefix == "casey-berlin"
        assert filename == "morning-walk-through-kreuzberg-1200x630.webp"

    def test_unbranded_slug(self):
        prefix, filename = make_slug("abstract pattern", 1080, 1080)
        assert prefix == "gen"
        assert "abstract-pattern" in filename
        assert "1080x1080" in filename

    def test_cdit_context(self):
        prefix, _ = make_slug("test", 100, 100, brand_context="@cdit")
        assert prefix == "cdit"

    def test_yorizon_context(self):
        prefix, _ = make_slug("test", 100, 100, brand_context="yorizon")
        assert prefix == "yorizon"

    def test_long_prompt_truncated(self):
        long_prompt = "a " * 100 + "very long prompt that exceeds sixty characters"
        _, filename = make_slug(long_prompt, 800, 600)
        # Slug part (before dimensions) should be <= 60 chars
        slug_part = filename.rsplit("-800x600", 1)[0]
        assert len(slug_part) <= 60

    def test_empty_prompt_fallback(self):
        _, filename = make_slug("", 800, 600)
        assert filename.startswith("image-")

    def test_collision_suffix_deterministic(self):
        data = b"some image bytes"
        s1 = make_collision_suffix(data)
        s2 = make_collision_suffix(data)
        assert s1 == s2
        assert len(s1) == 4

    def test_collision_suffix_differs(self):
        s1 = make_collision_suffix(b"image A")
        s2 = make_collision_suffix(b"image B")
        assert s1 != s2


class TestStoreImage:
    def test_stores_file_and_sidecar(self, tmp_path: Path):
        with patch("mcp_bildsprache.storage.settings") as mock_settings:
            mock_settings.image_storage_path = str(tmp_path)
            mock_settings.image_domain = "https://img.cdit-works.de"

            url = store_image(
                image_data=b"fake webp bytes",
                prompt="test image",
                width=800,
                height=600,
                model="test-model",
                cost_estimate="$0.00",
                brand_context="@casey.berlin",
            )

        assert url.startswith("https://img.cdit-works.de/casey-berlin/")
        assert url.endswith(".webp")

        # Check file exists
        webp_files = list(tmp_path.rglob("*.webp"))
        assert len(webp_files) == 1
        assert webp_files[0].read_bytes() == b"fake webp bytes"

        # Check sidecar exists
        json_files = list(tmp_path.rglob("*.json"))
        assert len(json_files) == 1
        sidecar = json.loads(json_files[0].read_text())
        assert sidecar["model"] == "test-model"
        assert sidecar["prompt"] == "test image"
        assert sidecar["brand_context"] == "@casey.berlin"
        assert sidecar["dimensions"] == "800x600"

    def test_collision_appends_hash(self, tmp_path: Path):
        with patch("mcp_bildsprache.storage.settings") as mock_settings:
            mock_settings.image_storage_path = str(tmp_path)
            mock_settings.image_domain = "https://img.cdit-works.de"

            url1 = store_image(
                image_data=b"image one",
                prompt="same prompt",
                width=800,
                height=600,
                model="m",
                cost_estimate="$0",
            )
            url2 = store_image(
                image_data=b"image two",
                prompt="same prompt",
                width=800,
                height=600,
                model="m",
                cost_estimate="$0",
            )

        assert url1 != url2
        webp_files = list(tmp_path.rglob("*.webp"))
        assert len(webp_files) == 2

    def test_creates_brand_directory(self, tmp_path: Path):
        with patch("mcp_bildsprache.storage.settings") as mock_settings:
            mock_settings.image_storage_path = str(tmp_path)
            mock_settings.image_domain = "https://img.cdit-works.de"

            store_image(
                image_data=b"bytes",
                prompt="test",
                width=100,
                height=100,
                model="m",
                cost_estimate="$0",
                brand_context="@storykeep",
            )

        assert (tmp_path / "storykeep").is_dir()

    def test_first_write_fails_retry_succeeds(self, tmp_path: Path):
        """If first write_bytes raises OSError, retry should succeed."""
        with patch("mcp_bildsprache.storage.settings") as mock_settings:
            mock_settings.image_storage_path = str(tmp_path)
            mock_settings.image_domain = "https://img.cdit-works.de"

            call_count = 0
            original_write = Path.write_bytes

            def flaky_write(self_path, data):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise OSError("Disk full")
                return original_write(self_path, data)

            with patch.object(Path, "write_bytes", flaky_write):
                url = store_image(
                    image_data=b"retry-data",
                    prompt="retry test",
                    width=800,
                    height=600,
                    model="m",
                    cost_estimate="$0",
                )

        assert url.startswith("https://img.cdit-works.de/")
        # The image file should exist after retry
        webp_files = list(tmp_path.rglob("*.webp"))
        assert len(webp_files) == 1

    def test_both_writes_fail_raises_storage_error(self, tmp_path: Path):
        """If both write attempts fail, StorageError is raised."""
        with patch("mcp_bildsprache.storage.settings") as mock_settings:
            mock_settings.image_storage_path = str(tmp_path)
            mock_settings.image_domain = "https://img.cdit-works.de"

            def always_fail(self_path, data):
                raise OSError("Disk permanently full")

            with patch.object(Path, "write_bytes", always_fail):
                with pytest.raises(StorageError, match="Failed to store image"):
                    store_image(
                        image_data=b"fail-data",
                        prompt="fail test",
                        width=800,
                        height=600,
                        model="m",
                        cost_estimate="$0",
                    )

    def test_sidecar_write_failure_logs_warning_but_returns_url(self, tmp_path: Path, caplog):
        """Sidecar write failure should not prevent returning the URL."""
        import logging

        with patch("mcp_bildsprache.storage.settings") as mock_settings:
            mock_settings.image_storage_path = str(tmp_path)
            mock_settings.image_domain = "https://img.cdit-works.de"

            original_write_text = Path.write_text

            def fail_json_write(self_path, text, *args, **kwargs):
                if str(self_path).endswith(".json"):
                    raise OSError("Cannot write sidecar")
                return original_write_text(self_path, text, *args, **kwargs)

            with patch.object(Path, "write_text", fail_json_write), \
                 caplog.at_level(logging.WARNING):
                url = store_image(
                    image_data=b"sidecar-fail-data",
                    prompt="sidecar test",
                    width=800,
                    height=600,
                    model="m",
                    cost_estimate="$0",
                )

        assert url.startswith("https://img.cdit-works.de/")
        assert "Failed to write sidecar" in caplog.text


class TestStoreRawImage:
    def test_store_raw_image_derives_path_correctly(self, tmp_path: Path):
        with patch("mcp_bildsprache.storage.settings") as mock_settings:
            mock_settings.image_storage_path = str(tmp_path)
            mock_settings.image_domain = "https://img.cdit-works.de"

            # First create the brand directory
            (tmp_path / "casey-berlin").mkdir()

            raw_url = store_raw_image(
                image_data=b"raw-jpeg-data",
                mime_type="image/jpeg",
                processed_file_path="https://img.cdit-works.de/casey-berlin/test-800x600.webp",
            )

        assert raw_url == "https://img.cdit-works.de/casey-berlin/test-800x600-raw.jpg"
        raw_files = list(tmp_path.rglob("*-raw.jpg"))
        assert len(raw_files) == 1

    def test_store_raw_image_unknown_mime_gets_bin_extension(self, tmp_path: Path):
        with patch("mcp_bildsprache.storage.settings") as mock_settings:
            mock_settings.image_storage_path = str(tmp_path)
            mock_settings.image_domain = "https://img.cdit-works.de"

            (tmp_path / "gen").mkdir()

            raw_url = store_raw_image(
                image_data=b"raw-unknown-data",
                mime_type="application/octet-stream",
                processed_file_path="https://img.cdit-works.de/gen/test-512x512.webp",
            )

        assert raw_url.endswith("-raw.bin")
        raw_files = list(tmp_path.rglob("*-raw.bin"))
        assert len(raw_files) == 1
