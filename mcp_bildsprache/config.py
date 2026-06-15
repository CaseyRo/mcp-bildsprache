"""Configuration loaded from environment variables."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # Image provider API keys.
    #
    # Active providers (May 2026 brand collapse): openai (raster default,
    # also accepts gpt-image-* hints) and gemini (diagram default for
    # generate_diagram).
    #
    # Disabled providers retained as recognised env vars: bfl, recraft.
    # Their dispatcher routing raises ProviderTemporarilyDisabled — modules
    # remain in-tree so re-enabling is a one-PR dispatcher swap. Setting
    # these env vars is safe (they're loaded but unused); leaving them
    # unset is also safe (the modules don't import them at module level).
    gemini_api_key: SecretStr = SecretStr("")
    bfl_api_key: SecretStr = SecretStr("")        # disabled, see note above
    recraft_api_key: SecretStr = SecretStr("")    # disabled, see note above
    openai_api_key: SecretStr = SecretStr("")

    # Recraft tier (free vs pro — affects license warnings).
    # Inert while Recraft is disabled at the dispatcher.
    recraft_tier: Literal["free", "pro"] = "free"

    # OpenAI image-generation model IDs (config so we can pin a snapshot like
    # gpt-image-2-2026-04-21 without a code change).
    openai_image_model: str = "gpt-image-2"
    openai_image_model_draft: str = "gpt-image-1-mini"

    # Image hosting
    enable_hosting: bool = True
    image_domain: str = "https://img.cdit-works.de"
    image_storage_path: str = "/data/images"

    # Generation outcome ledger (CDI-1264). Append-only JSONL on the same
    # persistent volume as the gallery, recording one record per generation
    # ATTEMPT (success AND failure) so attempts-vs-model-vs-outcome is
    # measurable over time — failures otherwise live only in ephemeral
    # container logs and vanish on restart. Writes are best-effort and never
    # break a generation. When ``ledger_path`` is unset (the default) it is
    # derived from ``image_storage_path`` as
    # ``<image_storage_path>/_ledger/generations.jsonl``.
    ledger_enabled: bool = True
    ledger_path: str = ""

    # Async dispatch+poll budget (CDI-1266). Clients reach this server through a
    # Cloudflare-managed MCP portal with a hard ~60s upstream read timeout we
    # cannot change; gpt-image-2 / Nano-Banana-Pro renders take 50-80s, so a
    # synchronous generate_image response is severed by the portal (-32001) even
    # though the render completes server-side.
    #
    # generate_image / generate_diagram dispatch the render in the BACKGROUND
    # (detached from the request scope so it survives request teardown) and then
    # inline-wait up to `sync_wait_seconds` for it to finish. Fast renders return
    # the hosted_url inline (backward compatible); slow ones return a {job_id,
    # status: "pending"} handle the caller polls with get_image_result. Set well
    # under the ~60s portal limit. `background=True` (per call) or
    # `sync_wait_seconds=0` forces an immediate job-handle return.
    sync_wait_seconds: int = 40
    # Long-poll ceiling for get_image_result(wait_seconds=...). Caller-supplied
    # wait_seconds is clamped to this so a poll can never exceed the portal budget.
    poll_wait_max_seconds: int = 55

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

    @property
    def resolved_ledger_path(self) -> Path:
        """Absolute path to the generation outcome ledger (JSONL).

        Defaults to ``<image_storage_path>/_ledger/generations.jsonl`` so the
        ledger lives on the same persistent volume as the gallery (survives a
        container restart). The ``_ledger`` directory sits *outside* any brand
        prefix, so the static mount never serves it. Set ``LEDGER_PATH`` to
        override with an explicit file path.
        """
        if self.ledger_path:
            return Path(self.ledger_path)
        return Path(self.image_storage_path) / "_ledger" / "generations.jsonl"


settings = Settings()
