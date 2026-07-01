"""Authentication for the Bildsprache MCP server.

Supports two authentication modes simultaneously via MultiAuth:

1. **Keycloak OIDC** (for Claude.ai connectors and other OAuth clients):
   The server proxies the full OAuth flow via OIDCProxy using pre-registered
   Keycloak client credentials. No Dynamic Client Registration (DCR) needed.

2. **Bearer token** (for Claude Code, n8n, and other direct clients):
   Static API key validation via Authorization: Bearer <key>.
"""

from __future__ import annotations

import hmac
import logging
import secrets

from fastmcp.server.auth import (
    AccessToken,
    MultiAuth,
    TokenVerifier,
)
from fastmcp.server.auth.oidc_proxy import OIDCProxy

logger = logging.getLogger(__name__)


class BearerTokenVerifier(TokenVerifier):
    """Validates incoming requests against a static API key.

    Uses constant-time comparison to prevent timing attacks.
    """

    def __init__(self, api_key: str) -> None:
        super().__init__()
        self._api_key = api_key

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self._api_key):
            logger.warning("Rejected request with invalid API key")
            return None

        return AccessToken(
            token=token,
            client_id="mcp-bildsprache-client",
            scopes=["all"],
        )


def build_cf_access_verifier(team_domain: str, aud: str) -> TokenVerifier | None:
    """A JWTVerifier for Cloudflare Access identity JWTs.

    CF Access injects a team-signed JWT (``Cf-Access-Jwt-Assertion``, ``aud`` =
    the Access application tag) on every request that passed its policy.
    Validating it against the team JWKS lets a GitHub-gated OAuth client (e.g.
    Claude Desktop via Managed OAuth) authenticate without a ``bmcp_`` bearer.
    The signature check is what makes this safe on the non-Access hostname too:
    a forged ``Cf-Access-Jwt-Assertion`` fails JWKS validation. Returns None if
    unconfigured (no behavior change).
    """
    if not (team_domain and aud):
        return None

    from fastmcp.server.auth.providers.jwt import JWTVerifier

    return JWTVerifier(
        jwks_uri=f"https://{team_domain}/cdn-cgi/access/certs",
        issuer=f"https://{team_domain}",
        audience=aud,
    )


def create_auth(
    api_key: str | None,
    keycloak_issuer: str,
    keycloak_audience: str,
    keycloak_client_id: str,
    keycloak_client_secret: str,
    base_url: str,
    cf_verifier: TokenVerifier | None = None,
    **_kwargs,
) -> MultiAuth:
    """Create the authentication provider.

    Returns a MultiAuth that accepts both:
    - Keycloak OIDC clients (Claude.ai) via OIDCProxy (server-side OAuth)
    - Bearer token clients (Claude Code, n8n) via static API key

    Args:
        api_key: Static API key for bearer token auth (None to skip).
        keycloak_issuer: Keycloak realm issuer URL
            (e.g. https://auth.cdit-works.de/realms/cdit-mcp).
        keycloak_audience: Expected JWT audience claim
            (e.g. mcp-bildsprache).
        keycloak_client_id: Pre-registered Keycloak client ID.
        keycloak_client_secret: Keycloak client secret.
        base_url: Public URL of this server
            (e.g. https://bildsprache.cdit-dev.de).
    """
    config_url = f"{keycloak_issuer}/.well-known/openid-configuration"

    oidc_auth = OIDCProxy(
        config_url=config_url,
        client_id=keycloak_client_id,
        client_secret=keycloak_client_secret,
        base_url=base_url,
    )

    verifiers: list[TokenVerifier] = []
    if api_key:
        verifiers.append(BearerTokenVerifier(api_key))
    if cf_verifier:
        verifiers.append(cf_verifier)

    return MultiAuth(server=oidc_auth, verifiers=verifiers)


def generate_api_key() -> str:
    """Generate a cryptographically secure API key."""
    return f"bmcp_{secrets.token_urlsafe(32)}"
