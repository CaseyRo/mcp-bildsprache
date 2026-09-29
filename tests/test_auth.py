"""Tests for authentication module."""

from __future__ import annotations

import hmac
from unittest.mock import MagicMock, patch

import pytest

from mcp_bildsprache.auth import BearerTokenVerifier, create_auth, generate_api_key


class TestBearerTokenVerifier:
    @pytest.mark.anyio
    async def test_bearer_verifier_accepts_valid_key(self):
        verifier = BearerTokenVerifier("my-secret-key")
        result = await verifier.verify_token("my-secret-key")
        assert result is not None
        assert result.client_id == "mcp-bildsprache-client"
        assert "all" in result.scopes

    @pytest.mark.anyio
    async def test_bearer_verifier_rejects_invalid_key(self):
        verifier = BearerTokenVerifier("my-secret-key")
        result = await verifier.verify_token("wrong-key")
        assert result is None

    @pytest.mark.anyio
    async def test_bearer_verifier_rejects_empty_string(self):
        verifier = BearerTokenVerifier("my-secret-key")
        result = await verifier.verify_token("")
        assert result is None

    @pytest.mark.anyio
    async def test_bearer_verifier_timing_safe(self):
        """Verify that hmac.compare_digest is used for comparison."""
        verifier = BearerTokenVerifier("my-secret-key")
        with patch("mcp_bildsprache.auth.hmac.compare_digest", wraps=hmac.compare_digest) as spy:
            await verifier.verify_token("my-secret-key")
            spy.assert_called_once_with("my-secret-key", "my-secret-key")


class TestCreateAuth:
    def test_create_auth_with_api_key(self):
        with patch("mcp_bildsprache.auth.OIDCProxy") as mock_oidc:
            mock_oidc.return_value = MagicMock()
            auth = create_auth(
                api_key="test-key",
                keycloak_issuer="https://auth.example.com/realms/test",
                keycloak_audience="mcp-bildsprache",
                keycloak_client_id="mcp-bildsprache",
                keycloak_client_secret="secret",
                base_url="https://bildsprache.example.com",
            )
        # MultiAuth should have been returned
        assert auth is not None

    def test_create_auth_without_api_key(self):
        with patch("mcp_bildsprache.auth.OIDCProxy") as mock_oidc:
            mock_oidc.return_value = MagicMock()
            auth = create_auth(
                api_key=None,
                keycloak_issuer="https://auth.example.com/realms/test",
                keycloak_audience="mcp-bildsprache",
                keycloak_client_id="mcp-bildsprache",
                keycloak_client_secret="secret",
                base_url="https://bildsprache.example.com",
            )
        # Should still return MultiAuth, just without bearer verifier
        assert auth is not None


class TestGenerateApiKey:
    def test_generate_api_key_format(self):
        key = generate_api_key()
        assert key.startswith("bmcp_")
        # URL-safe base64: only alphanumeric, hyphens, underscores
        suffix = key[5:]
        assert all(c.isalnum() or c in "-_" for c in suffix)

    def test_generate_api_key_uniqueness(self):
        keys = {generate_api_key() for _ in range(100)}
        assert len(keys) == 100


class TestBuildAuth:
    """server._build_auth: OIDC needs an issuer; bearer-only mode stays valid."""

    def _settings(self, **kw):
        from pydantic import SecretStr

        s = MagicMock()
        s.transport = "http"
        s.mcp_bildsprache_api_key = SecretStr("bmcp_test")
        s.keycloak_client_secret = SecretStr(kw.get("secret", ""))
        s.keycloak_issuer = kw.get("issuer", "")
        s.cf_access_team_domain = ""
        s.cf_access_aud = ""
        return s

    def test_oidc_without_issuer_refuses_to_start(self):
        from mcp_bildsprache import server

        with patch.object(server, "settings", self._settings(secret="secret")):
            with pytest.raises(SystemExit, match="KEYCLOAK_ISSUER"):
                server._build_auth()

    def test_bearer_only_without_issuer(self):
        from mcp_bildsprache import server

        with patch.object(server, "settings", self._settings()):
            auth = server._build_auth()
        assert isinstance(auth, BearerTokenVerifier)
