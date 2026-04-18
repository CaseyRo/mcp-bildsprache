"""Tests for the gallery sidecar scanner, filter/sort, and pagination."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp_bildsprache.gallery.index import (
    GalleryIndex,
    scan_index,
)


def _write_entry(
    root: Path,
    brand: str,
    slug: str,
    width: int,
    height: int,
    *,
    prompt: str = "a prompt",
    platform: str | None = None,
    cost_estimate: str = "$0.01",
    model: str = "flux-2-max",
    created_at: str | None = None,
    file_size_bytes: int | None = None,
    mtime: float | None = None,
    include_webp: bool = True,
    include_sidecar: bool = True,
    domain: str = "https://img.example.com",
) -> tuple[Path, Path]:
    d = root / brand
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{slug}-{width}x{height}"
    webp = d / f"{stem}.webp"
    sidecar = d / f"{stem}.json"

    if include_webp:
        webp.write_bytes(b"fakewebpbytes")
    if include_sidecar:
        body: dict = {
            "prompt": prompt,
            "model": model,
            "cost_estimate": cost_estimate,
            "dimensions": f"{width}x{height}",
            "hosted_url": f"{domain}/{brand}/{stem}.webp",
        }
        if platform is not None:
            body["platform"] = platform
        if created_at is not None:
            body["generated_at"] = created_at
        if file_size_bytes is not None:
            body["file_size_bytes"] = file_size_bytes
        sidecar.write_text(json.dumps(body))
    if mtime is not None and sidecar.exists():
        os.utime(sidecar, (mtime, mtime))
    return webp, sidecar


class TestScanIndex:
    def test_entry_from_sidecar(self, tmp_path: Path):
        _write_entry(tmp_path, "cdit", "hello", 1200, 630, prompt="Hello FOREST")
        entries = scan_index(tmp_path, "https://img.example.com")
        assert len(entries) == 1
        e = entries[0]
        assert e.path == "cdit/hello-1200x630.webp"
        assert e.brand == "cdit"
        assert e.hosted_url == "https://img.example.com/cdit/hello-1200x630.webp"
        assert e.prompt_lower == "hello forest"
        assert e.width == 1200
        assert e.height == 630

    def test_missing_sidecar_skipped(self, tmp_path: Path):
        _write_entry(tmp_path, "cdit", "lonely", 100, 100, include_sidecar=False)
        assert scan_index(tmp_path, "https://img.example.com") == []

    def test_missing_platform_tolerated(self, tmp_path: Path):
        _write_entry(tmp_path, "cdit", "a", 100, 100, platform=None)
        [e] = scan_index(tmp_path, "https://img.example.com")
        assert e.platform is None

    def test_mtime_fallback_when_generated_at_absent(self, tmp_path: Path):
        # Older sidecar without generated_at
        mtime = 1_600_000_000.0
        _write_entry(tmp_path, "cdit", "old", 100, 100, created_at=None, mtime=mtime)
        [e] = scan_index(tmp_path, "https://img.example.com")
        assert abs(e.created_at.timestamp() - mtime) < 2

    def test_unreadable_json_skipped(self, tmp_path: Path):
        d = tmp_path / "cdit"
        d.mkdir(parents=True)
        (d / "bad-100x100.json").write_text("not-json")
        (d / "bad-100x100.webp").write_bytes(b"x")
        # Should not raise, just return empty.
        assert scan_index(tmp_path, "https://img.example.com") == []


def _build_index(tmp_path: Path) -> GalleryIndex:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Three entries across two brands, three platforms, varied costs/sizes.
    _write_entry(
        tmp_path, "cdit", "forest", 1600, 900,
        prompt="a deep forest scene", platform="blog-hero",
        cost_estimate="$0.05", file_size_bytes=200_000,
        created_at=base.isoformat(),
    )
    _write_entry(
        tmp_path, "cdit", "coastline", 1200, 630,
        prompt="misty coastline", platform="og-image",
        cost_estimate="$0.03", file_size_bytes=150_000,
        created_at=(base + timedelta(days=10)).isoformat(),
    )
    _write_entry(
        tmp_path, "casey-berlin", "walk", 1200, 1200,
        prompt="morning walk", platform="linkedin-post",
        cost_estimate="$0.01", file_size_bytes=120_000,
        created_at=(base + timedelta(days=20)).isoformat(),
    )

    idx = GalleryIndex(data_dir=tmp_path, public_base_url="https://img.example.com")
    idx.refresh()
    return idx


class TestFilter:
    def test_brand_list(self, tmp_path: Path):
        idx = _build_index(tmp_path)
        total, items = idx.filter_and_sort(brand=["cdit"])
        assert total == 2
        assert all(e.brand == "cdit" for e in items)

    def test_platform_exact(self, tmp_path: Path):
        idx = _build_index(tmp_path)
        total, items = idx.filter_and_sort(platform="og-image")
        assert total == 1
        assert items[0].platform == "og-image"

    def test_date_range(self, tmp_path: Path):
        idx = _build_index(tmp_path)
        total, items = idx.filter_and_sort(
            date_from="2026-01-05", date_to="2026-01-15"
        )
        assert total == 1
        assert items[0].prompt == "misty coastline"

    def test_q_case_insensitive(self, tmp_path: Path):
        idx = _build_index(tmp_path)
        total, items = idx.filter_and_sort(q="FOREST")
        assert total == 1
        assert items[0].prompt == "a deep forest scene"

    def test_q_excludes_non_matches(self, tmp_path: Path):
        idx = _build_index(tmp_path)
        total, _ = idx.filter_and_sort(q="doesnotmatchanything")
        assert total == 0

    def test_min_dimensions(self, tmp_path: Path):
        idx = _build_index(tmp_path)
        total, items = idx.filter_and_sort(min_width=1600)
        assert total == 1
        assert items[0].width == 1600

    def test_combined(self, tmp_path: Path):
        idx = _build_index(tmp_path)
        total, items = idx.filter_and_sort(
            brand=["cdit"], q="forest", min_height=500,
        )
        assert total == 1
        assert items[0].prompt == "a deep forest scene"


class TestSort:
    def test_default_created_desc(self, tmp_path: Path):
        idx = _build_index(tmp_path)
        total, items = idx.filter_and_sort()
        assert total == 3
        for i in range(len(items) - 1):
            assert items[i].created_at >= items[i + 1].created_at

    def test_created_asc(self, tmp_path: Path):
        idx = _build_index(tmp_path)
        _, items = idx.filter_and_sort(sort="created_asc")
        for i in range(len(items) - 1):
            assert items[i].created_at <= items[i + 1].created_at

    def test_cost_desc(self, tmp_path: Path):
        idx = _build_index(tmp_path)
        _, items = idx.filter_and_sort(sort="cost_desc")
        assert items[0].cost_estimate == "$0.05"
        assert items[-1].cost_estimate == "$0.01"

    def test_size_desc(self, tmp_path: Path):
        idx = _build_index(tmp_path)
        _, items = idx.filter_and_sort(sort="size_desc")
        sizes = [e.file_size for e in items]
        assert sizes == sorted(sizes, reverse=True)


class TestPagination:
    def test_offset_and_limit(self, tmp_path: Path):
        idx = _build_index(tmp_path)
        total, page1 = idx.filter_and_sort(limit=2, offset=0)
        assert total == 3
        assert len(page1) == 2
        total2, page2 = idx.filter_and_sort(limit=2, offset=2)
        assert total2 == 3
        assert len(page2) == 1
        # No overlap between pages.
        seen = {e.path for e in page1}
        assert page2[0].path not in seen

    def test_limit_is_clamped_to_500(self, tmp_path: Path):
        # Seed 600 entries quickly.
        for i in range(600):
            brand = "cdit" if i % 2 == 0 else "casey-berlin"
            _write_entry(tmp_path, brand, f"e{i}", 100, 100, prompt=f"n{i}")
        idx = GalleryIndex(data_dir=tmp_path, public_base_url="https://x")
        idx.refresh()
        total, items = idx.filter_and_sort(limit=1000)
        assert total == 600
        assert len(items) == 500
