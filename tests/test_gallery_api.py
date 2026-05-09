"""Tests for the Starlette gallery sub-app and the TailnetOnlyMiddleware."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from mcp_bildsprache.gallery.app import create_gallery_app
from mcp_bildsprache.gallery.middleware import TailnetOnlyMiddleware


def _write_sidecar(
    root: Path,
    brand: str,
    slug: str,
    width: int,
    height: int,
    *,
    prompt: str = "prompt",
    platform: str | None = None,
    created_at: str | None = None,
) -> None:
    d = root / brand
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{slug}-{width}x{height}"
    (d / f"{stem}.webp").write_bytes(b"x")
    body: dict = {
        "prompt": prompt,
        "model": "flux-2-max",
        "cost_estimate": "$0.03",
        "dimensions": f"{width}x{height}",
        "hosted_url": f"https://img.example.com/{brand}/{stem}.webp",
    }
    if platform is not None:
        body["platform"] = platform
    if created_at is not None:
        body["generated_at"] = created_at
    (d / f"{stem}.json").write_text(json.dumps(body))


def _seed(tmp_path: Path) -> Path:
    _write_sidecar(
        tmp_path, "cdit", "forest", 1200, 630,
        prompt="forest", platform="og-image",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
    )
    _write_sidecar(
        tmp_path, "casey-berlin", "walk", 1200, 1200,
        prompt="morning walk",
        created_at=datetime(2026, 1, 10, tzinfo=timezone.utc).isoformat(),
    )
    return tmp_path


class TestGalleryAPI:
    def test_shell_served(self, tmp_path: Path):
        app = create_gallery_app(
            data_dir=_seed(tmp_path),
            public_base_url="https://img.example.com",
            reindex_interval_seconds=3600,
        )
        with TestClient(app) as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]
            assert "<html" in resp.text.lower()

    def test_list_default(self, tmp_path: Path):
        app = create_gallery_app(
            data_dir=_seed(tmp_path),
            public_base_url="https://img.example.com",
            reindex_interval_seconds=3600,
        )
        with TestClient(app) as client:
            resp = client.get("/api/images")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 2
            assert data["limit"] == 100
            assert data["offset"] == 0
            # Newest first.
            assert data["items"][0]["prompt"] == "morning walk"
            assert data["items"][1]["prompt"] == "forest"

    def test_list_filters(self, tmp_path: Path):
        app = create_gallery_app(
            data_dir=_seed(tmp_path),
            public_base_url="https://img.example.com",
            reindex_interval_seconds=3600,
        )
        with TestClient(app) as client:
            resp = client.get("/api/images?brand=cdit&q=FOREST")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert data["items"][0]["brand"] == "cdit"

    def test_single_entry_found(self, tmp_path: Path):
        app = create_gallery_app(
            data_dir=_seed(tmp_path),
            public_base_url="https://img.example.com",
            reindex_interval_seconds=3600,
        )
        with TestClient(app) as client:
            resp = client.get("/api/images/cdit/forest-1200x630.webp")
            assert resp.status_code == 200
            assert resp.json()["prompt"] == "forest"

    def test_single_entry_not_found(self, tmp_path: Path):
        app = create_gallery_app(
            data_dir=_seed(tmp_path),
            public_base_url="https://img.example.com",
            reindex_interval_seconds=3600,
        )
        with TestClient(app) as client:
            resp = client.get("/api/images/does/not/exist.webp")
            assert resp.status_code == 404

    def test_reindex_picks_up_new_entry(self, tmp_path: Path):
        _seed(tmp_path)
        app = create_gallery_app(
            data_dir=tmp_path,
            public_base_url="https://img.example.com",
            reindex_interval_seconds=3600,
        )
        with TestClient(app) as client:
            # Initial total is 2
            assert client.get("/api/images").json()["total"] == 2

            # Write a new pair after startup.
            _write_sidecar(
                tmp_path, "cdit", "newone", 100, 100,
                prompt="brand new",
                created_at=datetime(2026, 2, 1, tzinfo=timezone.utc).isoformat(),
            )

            # Reindex and confirm it's visible.
            r = client.post("/api/reindex")
            assert r.status_code == 200
            assert r.json()["total"] == 3
            items = client.get("/api/images").json()["items"]
            assert any(i["prompt"] == "brand new" for i in items)

    def test_static_files_served(self, tmp_path: Path):
        app = create_gallery_app(
            data_dir=_seed(tmp_path),
            public_base_url="https://img.example.com",
            reindex_interval_seconds=3600,
        )
        with TestClient(app) as client:
            resp = client.get("/static/fflate.min.js")
            assert resp.status_code == 200
            # Starlette's StaticFiles picks the mime type from the extension.
            assert "javascript" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# TailnetOnlyMiddleware
# ---------------------------------------------------------------------------


def _build_parent_app(tmp_path: Path, *, tailnet_host: str | None) -> Starlette:
    """Parent app mirroring the server.py shape: /gallery + /mcp echo."""
    gallery = create_gallery_app(
        data_dir=_seed(tmp_path),
        public_base_url="https://img.example.com",
        reindex_interval_seconds=3600,
    )

    async def mcp(_req):
        return JSONResponse({"ok": True})

    parent = Starlette(
        routes=[
            Mount("/gallery", app=gallery),
            Route("/mcp", mcp, methods=["GET", "POST"]),
        ],
        lifespan=gallery.router.lifespan_context,
    )
    parent.add_middleware(TailnetOnlyMiddleware, allowed_host=tailnet_host)
    return parent


class TestTailnetMiddleware:
    def test_public_host_returns_404_for_gallery(self, tmp_path: Path):
        app = _build_parent_app(tmp_path, tailnet_host="bs-gallery.tailnet.ts.net")
        with TestClient(app) as client:
            resp = client.get("/gallery/", headers={"Host": "bildsprache.cdit-dev.de"})
            assert resp.status_code == 404

    def test_public_host_reaches_mcp(self, tmp_path: Path):
        app = _build_parent_app(tmp_path, tailnet_host="bs-gallery.tailnet.ts.net")
        with TestClient(app) as client:
            resp = client.get("/mcp", headers={"Host": "bildsprache.cdit-dev.de"})
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

    def test_tailnet_host_reaches_gallery(self, tmp_path: Path):
        app = _build_parent_app(tmp_path, tailnet_host="bs-gallery.tailnet.ts.net")
        with TestClient(app) as client:
            resp = client.get("/gallery/", headers={"Host": "bs-gallery.tailnet.ts.net"})
            assert resp.status_code == 200
            assert "<html" in resp.text.lower()

    def test_noop_when_host_unset(self, tmp_path: Path):
        app = _build_parent_app(tmp_path, tailnet_host=None)
        with TestClient(app) as client:
            resp = client.get("/gallery/", headers={"Host": "anything.example.com"})
            assert resp.status_code == 200

    def test_host_with_port_matches(self, tmp_path: Path):
        """`Host: foo:1234` should still match `foo` (port stripped)."""
        app = _build_parent_app(tmp_path, tailnet_host="bs-gallery.tailnet.ts.net")
        with TestClient(app) as client:
            resp = client.get(
                "/gallery/",
                headers={"Host": "bs-gallery.tailnet.ts.net:443"},
            )
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# _mount_gallery regression — initial scan + lifespan-wrapped reindex
# (added 2026-05-09 after production showed the gallery index empty
# despite v0.3.28 having been up for 17 minutes — the sub-app lifespan
# never fired because FastMCP's parent lifespan doesn't delegate to
# mounted sub-apps).
# ---------------------------------------------------------------------------


class TestMountGalleryFires:
    """The mount helper must populate the index synchronously and arrange
    for the periodic reindex to run regardless of the parent's lifespan
    shape."""

    def _make_data_dir_with_one_sidecar(self, tmp_path: Path) -> Path:
        d = tmp_path / "images"
        d.mkdir()
        brand_dir = d / "casey"
        brand_dir.mkdir()
        sidecar = brand_dir / "test-1024x1024.json"
        sidecar.write_text(json.dumps({
            "model": "gpt-image-2",
            "prompt": "test",
            "dimensions": "1024x1024",
            "brand_context": "casey",
        }))
        webp = brand_dir / "test-1024x1024.webp"
        webp.write_bytes(b"fake-webp")
        return d

    def test_initial_scan_is_synchronous(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """``_mount_gallery`` must call ``index.refresh()`` synchronously so
        the very first request to ``/gallery/api/images`` returns populated
        data — even before the parent app's lifespan has executed."""
        from starlette.applications import Starlette

        from mcp_bildsprache.config import settings
        from mcp_bildsprache.server import _mount_gallery

        data_dir = self._make_data_dir_with_one_sidecar(tmp_path)
        monkeypatch.setattr(settings, "gallery_enabled", True)
        monkeypatch.setattr(settings, "image_storage_path", str(data_dir))
        monkeypatch.setattr(settings, "image_domain", "https://img.cdit-works.de")
        monkeypatch.setattr(settings, "gallery_tailnet_hostname", "test.example.com")
        monkeypatch.setattr(settings, "gallery_reindex_interval_seconds", 300)

        parent = Starlette()
        _mount_gallery(parent)

        # The gallery sub-app is now in parent.router.routes; pull it back out
        # to inspect the index state without making an HTTP call.
        from starlette.routing import Mount

        gallery_mount = next(
            (r for r in parent.router.routes if isinstance(r, Mount) and r.path == "/gallery"),
            None,
        )
        assert gallery_mount is not None
        gallery_app = gallery_mount.app
        index = gallery_app.state.gallery_index

        # The index must already be populated from the synchronous scan.
        assert index.total() == 1
        assert index.entries[0].brand == "casey"

    @pytest.mark.anyio
    async def test_lifespan_wrap_drives_reindex_loop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The wrapped lifespan must start the periodic reindex task even
        when the parent app's own lifespan is a no-op (which is the case
        when FastMCP's http_app() doesn't delegate to mounted sub-apps).

        We simulate that with a vanilla Starlette parent (whose default
        lifespan does nothing useful), drive the lifespan manually via the
        ASGI lifespan protocol, and assert that the reindex task starts.
        """
        import asyncio
        from starlette.applications import Starlette

        from mcp_bildsprache.config import settings
        from mcp_bildsprache.server import _mount_gallery

        data_dir = self._make_data_dir_with_one_sidecar(tmp_path)
        monkeypatch.setattr(settings, "gallery_enabled", True)
        monkeypatch.setattr(settings, "image_storage_path", str(data_dir))
        monkeypatch.setattr(settings, "image_domain", "https://img.cdit-works.de")
        monkeypatch.setattr(settings, "gallery_tailnet_hostname", "test.example.com")
        # 1-second interval so the test doesn't wait forever.
        monkeypatch.setattr(settings, "gallery_reindex_interval_seconds", 1)

        parent = Starlette()
        _mount_gallery(parent)

        # Capture asyncio.create_task to observe what the wrapped lifespan
        # spawns. We can't just check task counts because the test runner
        # has its own tasks.
        spawned: list[asyncio.Task] = []
        original_create_task = asyncio.create_task

        def _capture(coro, **kwargs):
            t = original_create_task(coro, **kwargs)
            spawned.append(t)
            return t

        monkeypatch.setattr(asyncio, "create_task", _capture)

        # Drive the wrapped lifespan as an async-context-manager (this is
        # what Starlette's ASGI lifespan handler does internally).
        async with parent.router.lifespan_context(parent):
            # During the lifespan body, exactly one task should have been
            # created (the _reindex_loop). It should not yet be done.
            assert any(
                "_reindex_loop" in (t.get_name() or "")
                or "_reindex_loop" in repr(t.get_coro())
                for t in spawned
            ), f"expected _reindex_loop task to be spawned; saw {[repr(t.get_coro()) for t in spawned]}"

        # After lifespan exits, all spawned tasks must be cancelled or done.
        for t in spawned:
            assert t.done(), f"task {t!r} should be cancelled after lifespan exit"
