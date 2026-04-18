"""Starlette sub-app for the gallery.

Mounted at `/gallery` on the main FastMCP app. Exposes:

- `GET  /`                     — HTML shell
- `GET  /static/<path>`        — vendored JS / CSS / fflate
- `GET  /api/images`           — filtered, paged list
- `GET  /api/images/<path>`    — single entry for deep links
- `POST /api/reindex`          — synchronous rescan
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from mcp_bildsprache.gallery.index import GalleryIndex, _reindex_loop

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def _entry_to_json(entry) -> dict:
    return entry.to_public_dict()


def _parse_int(value: str | None, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_brand_param(raw_values: list[str]) -> list[str]:
    """Accept repeated ?brand=foo&brand=bar AND ?brand=foo,bar."""
    out: list[str] = []
    for val in raw_values:
        for part in val.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _build_routes(index: GalleryIndex) -> list:
    async def index_html(_: Request) -> Response:
        html_path = STATIC_DIR / "index.html"
        if not html_path.exists():
            return Response("gallery frontend not bundled", status_code=500)
        return FileResponse(html_path, media_type="text/html")

    async def list_images(request: Request) -> Response:
        qs = request.query_params
        brand_values = qs.getlist("brand") if hasattr(qs, "getlist") else [
            v for k, v in qs.multi_items() if k == "brand"
        ]
        brand = _parse_brand_param(brand_values) or None
        platform = qs.get("platform") or None
        q = qs.get("q") or None
        date_from = qs.get("from") or None
        date_to = qs.get("to") or None
        min_width = _parse_int(qs.get("min_width"), default=0) or None
        min_height = _parse_int(qs.get("min_height"), default=0) or None
        sort = qs.get("sort") or "created_desc"
        limit = _parse_int(qs.get("limit"), default=100)
        offset = _parse_int(qs.get("offset"), default=0)

        total, paged = index.filter_and_sort(
            brand=brand,
            platform=platform,
            date_from=date_from,
            date_to=date_to,
            q=q,
            min_width=min_width,
            min_height=min_height,
            sort=sort,
            limit=limit,
            offset=offset,
        )

        # Reflect the clamped limit (max 500) in the response.
        effective_limit = min(max(limit, 0), 500)

        return JSONResponse(
            {
                "total": total,
                "limit": effective_limit,
                "offset": max(offset, 0),
                "items": [_entry_to_json(e) for e in paged],
            }
        )

    async def get_image(request: Request) -> Response:
        path = request.path_params.get("path", "")
        entry = index.get(path)
        if entry is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        return JSONResponse(_entry_to_json(entry))

    async def reindex(_: Request) -> Response:
        total = index.refresh()
        return JSONResponse({"total": total})

    routes = [
        Route("/", index_html, methods=["GET"]),
        Route("/api/images", list_images, methods=["GET"]),
        Route("/api/images/{path:path}", get_image, methods=["GET"]),
        Route("/api/reindex", reindex, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=str(STATIC_DIR)), name="gallery-static"),
    ]
    return routes


def create_gallery_app(
    data_dir: Path,
    public_base_url: str,
    reindex_interval_seconds: int = 300,
) -> Starlette:
    """Build the Starlette sub-app with its own lifespan.

    Lifespan:
      * synchronous first scan before accepting requests,
      * spawn `_reindex_loop` as a background task,
      * cancel on shutdown.
    """
    index = GalleryIndex(data_dir=data_dir, public_base_url=public_base_url)

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        index.refresh()  # block until we have an index
        task = asyncio.create_task(_reindex_loop(index, reindex_interval_seconds))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = Starlette(routes=_build_routes(index), lifespan=lifespan)
    # Expose the index on the app state for tests and diagnostics.
    app.state.gallery_index = index
    return app
