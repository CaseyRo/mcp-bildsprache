"""Tests for configuration module."""

from __future__ import annotations

import logging
from unittest.mock import patch


from mcp_bildsprache.config import Settings


class TestDefaultSettings:
    def test_default_settings_values(self):
        """Verify default values for transport, host, port, enable_hosting."""
        with patch.dict("os.environ", {}, clear=True):
            s = Settings()
        assert s.transport == "stdio"
        assert s.host == "127.0.0.1"
        assert s.port == 8000
        assert s.enable_hosting is True


class TestMissingApiKeysWarning:
    def test_missing_api_keys_warns(self, caplog):
        """With no API keys set, a warning is logged listing missing keys."""
        with patch.dict("os.environ", {}, clear=True):
            with caplog.at_level(logging.WARNING):
                Settings()
        assert "Missing API keys" in caplog.text
        assert "GEMINI_API_KEY" in caplog.text
        assert "BFL_API_KEY" in caplog.text
        assert "RECRAFT_API_KEY" in caplog.text


class TestEnsureApiKey:
    def test_ensure_api_key_returns_configured_key(self):
        with patch.dict("os.environ", {"MCP_BILDSPRACHE_API_KEY": "my-fixed-key"}, clear=True):
            s = Settings()
        assert s.ensure_api_key() == "my-fixed-key"

    def test_ensure_api_key_generates_when_empty(self):
        with patch.dict("os.environ", {}, clear=True):
            s = Settings()
        key = s.ensure_api_key()
        assert key.startswith("bmcp_")
        # Should be stored on the instance now
        assert s.mcp_bildsprache_api_key == key


class TestBaseUrl:
    def test_base_url_from_public_url(self):
        with patch.dict(
            "os.environ",
            {"MCP_BILDSPRACHE_PUBLIC_URL": "https://bildsprache.cdit-dev.de/"},
            clear=True,
        ):
            s = Settings()
        # Trailing slash should be stripped
        assert s.base_url == "https://bildsprache.cdit-dev.de"

    def test_base_url_fallback_to_host_port(self):
        with patch.dict("os.environ", {}, clear=True):
            s = Settings()
        assert s.base_url == "http://127.0.0.1:8000"
