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
