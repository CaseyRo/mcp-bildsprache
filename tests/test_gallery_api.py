"""Tests for the Starlette gallery sub-app and the TailnetOnlyMiddleware."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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
