"""Shared types for the Bildsprache MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Unified return type from all image generation providers."""

    image_data: bytes
    mime_type: str
    model: str
    cost_estimate: str


@dataclass(frozen=True, slots=True)
class IdentitySlot:
    """A single slot in an identity pack (e.g. "casey", "fimme", "sien").

    ``files`` is the ordered list of image paths on disk for this slot.
    ``tags`` are free-form labels (e.g. "person", "dog") kept for debugging
    and future use — resolution does not currently read them.
    ``unavailable`` is set at load time when one or more declared files are
    missing on disk; the loader logs a WARN per missing file.
    """

    name: str
    files: tuple[Path, ...]
    tags: tuple[str, ...] = ()
    unavailable: bool = False


@dataclass(frozen=True, slots=True)
class IdentityPack:
    """A per-brand identity pack loaded from `/data/identity/<brand>/manifest.json`.

    ``slots`` preserves manifest declaration order — resolution returns
    slot paths in that order for determinism.
    """

    brand: str
    slots: tuple[IdentitySlot, ...]
    always_include: tuple[str, ...] = ()
    include_if_prompt_matches: dict[str, tuple[str, ...]] = field(default_factory=dict)
    exclude_if_prompt_matches: dict[str, tuple[str, ...]] = field(default_factory=dict)
