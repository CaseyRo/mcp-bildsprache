"""CF Access auth bridge: header rewrite + verifier gating."""
from mcp_bildsprache.auth import build_cf_access_verifier
from mcp_bildsprache.server import CfAccessHeaderMiddleware


async def _run(headers):
    seen = {}

    async def inner(scope, receive, send):
        seen["headers"] = dict(scope["headers"])

    await CfAccessHeaderMiddleware(inner)({"type": "http", "headers": headers}, None, None)
    return seen["headers"]


async def test_cf_jwt_becomes_bearer():
    h = await _run([(b"cf-access-jwt-assertion", b"JWT123")])
    assert h[b"authorization"] == b"Bearer JWT123"


async def test_bmcp_bearer_wins_over_cf_header():
    # An explicit bmcp_ bearer must not be clobbered by the CF header.
    h = await _run(
        [(b"cf-access-jwt-assertion", b"JWT123"), (b"authorization", b"Bearer bmcp_real")]
    )
    assert h[b"authorization"] == b"Bearer bmcp_real"


async def test_no_cf_header_is_passthrough():
    h = await _run([(b"authorization", b"Bearer bmcp_real")])
    assert h[b"authorization"] == b"Bearer bmcp_real"


async def test_non_http_scope_does_not_crash():
    seen = {}

    async def inner(scope, receive, send):
        seen["ok"] = True

    await CfAccessHeaderMiddleware(inner)({"type": "lifespan"}, None, None)
    assert seen["ok"]


def test_verifier_gating():
    assert build_cf_access_verifier("", "") is None
    assert build_cf_access_verifier("cdit-dev.cloudflareaccess.com", "") is None
    assert build_cf_access_verifier("", "aud123") is None
    assert build_cf_access_verifier("cdit-dev.cloudflareaccess.com", "aud123") is not None
