"""Shared types for the Bildsprache MCP server."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Unified return type from all image generation providers."""

    image_data: bytes
    mime_type: str
    model: str
    cost_estimate: str
