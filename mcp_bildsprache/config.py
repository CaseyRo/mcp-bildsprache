"""Configuration loaded from environment variables."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # Image provider API keys
    gemini_api_key: SecretStr = SecretStr("")
    bfl_api_key: SecretStr = SecretStr("")
    recraft_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")

    # Recraft tier (free vs pro — affects license warnings)
    recraft_tier: Literal["free", "pro"] = "free"

    # OpenAI image-generation model IDs (config so we can pin a snapshot like
    # gpt-image-2-2026-04-21 without a code change).
    openai_image_model: str = "gpt-image-2"
    openai_image_model_draft: str = "gpt-image-1-mini"

    # Image hosting
    enable_hosting: bool = True
    image_domain: str = "https://img.cdit-works.de"
    image_storage_path: str = "/data/images"

    # Identity packs (personal likeness reference images; private volume)
    identity_dir: Path = Path("/data/identity")
    identity_enabled: bool = True

    # Server transport
    transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000

    # MCP server auth
    mcp_bildsprache_api_key: SecretStr = SecretStr("")
    mcp_bildsprache_public_url: str = ""

    # Keycloak OIDC
    keycloak_issuer: str = "https://auth.cdit-works.de/realms/cdit-mcp"
    keycloak_audience: str = "mcp-bildsprache"
    keycloak_client_id: str = "mcp-bildsprache"
    keycloak_client_secret: SecretStr = SecretStr("")

    # Gallery (Tailnet-only browse/download UI)
    gallery_enabled: bool = True
    gallery_reindex_interval_seconds: int = 300
    gallery_tailnet_hostname: str | None = None
    gallery_soft_zip_cap_mb: int = 250

    model_config = {"env_prefix": "", "case_sensitive": False}

    def model_post_init(self, __context: Any) -> None:
        missing = []
        if not self.gemini_api_key.get_secret_value():
            missing.append("GEMINI_API_KEY")
        if not self.bfl_api_key.get_secret_value():
            missing.append("BFL_API_KEY")
        if not self.recraft_api_key.get_secret_value():
            missing.append("RECRAFT_API_KEY")
        if not self.openai_api_key.get_secret_value():
            missing.append("OPENAI_API_KEY")
        if missing:
            logger.warning("Missing API keys: %s — those providers will be unavailable", ", ".join(missing))

    def ensure_api_key(self) -> str:
        """Return the API key, generating one if not configured."""
        existing = self.mcp_bildsprache_api_key.get_secret_value()
        if existing:
            return existing

        from mcp_bildsprache.auth import generate_api_key

        key = generate_api_key()
        self.mcp_bildsprache_api_key = SecretStr(key)
        logger.warning("Generated API key: %s (set MCP_BILDSPRACHE_API_KEY to persist)", key)
        return key

    @property
    def base_url(self) -> str:
        """Public URL for OAuth metadata, or computed from host:port."""
        if self.mcp_bildsprache_public_url:
            return self.mcp_bildsprache_public_url.rstrip("/")
        return f"http://{self.host}:{self.port}"


settings = Settings()
