"""Tailnet-only gate for the gallery routes.

The gallery is mounted at `/gallery` on the main ASGI app. Public
hostnames (the Cloudflare tunnel) MUST NOT serve `/gallery/*` — only
the docktail-exposed Tailnet hostname is allowed. Non-gallery routes
(`/mcp`, static images) are passed through untouched.
"""

from __future__ import annotations

import logging

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

_GALLERY_PREFIX = "/gallery"
_STARTUP_WARN_EMITTED = False


def _extract_host(scope: Scope) -> str | None:
    """Return the request's `Host` header (lower-case), or None."""
    for key, value in scope.get("headers", []):
        if key == b"host":
            try:
                return value.decode("latin-1").split(":", 1)[0].lower()
            except UnicodeDecodeError:
                return None
    return None


class TailnetOnlyMiddleware:
    """Reject /gallery/* requests whose Host header isn't the Tailnet hostname.

    When `allowed_host` is None, the middleware is a no-op (dev convenience)
    and emits a single startup WARN. Rejection uses HTTP 404 — not 403 —
    so that the gallery's existence isn't advertised on the public hostname.
    """

    def __init__(self, app: ASGIApp, allowed_host: str | None) -> None:
        self.app = app
        self.allowed_host = allowed_host.lower() if allowed_host else None
        global _STARTUP_WARN_EMITTED
        if self.allowed_host is None and not _STARTUP_WARN_EMITTED:
            logger.warning(
                "Gallery tailnet hostname not configured — /gallery/* is reachable "
                "from any hostname. Set GALLERY_TAILNET_HOSTNAME in production."
            )
            _STARTUP_WARN_EMITTED = True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if not (path == _GALLERY_PREFIX or path.startswith(_GALLERY_PREFIX + "/")):
            await self.app(scope, receive, send)
            return

        if self.allowed_host is None:
            # No-op: dev convenience — allow through.
            await self.app(scope, receive, send)
            return

        host = _extract_host(scope)
        if host == self.allowed_host:
            await self.app(scope, receive, send)
            return

        response = PlainTextResponse("Not Found", status_code=404)
        await response(scope, receive, send)
