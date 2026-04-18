"""Identity pack loader and resolver for brand-scoped reference images.

Identity packs live on the `identity-data` Docker volume, mounted read-only
at `/data/identity/<brand-dir>/`. Each brand has its own `manifest.json`
declaring reference-image slots and resolution rules (see
`docs/identity/README.md`).

Nothing in this module reaches the network or touches the filesystem outside
of the configured root. `resolve_identity` is a pure function of (pack,
prompt) and is safe to call per-request.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from mcp_bildsprache.slugs import BRAND_PREFIXES
from mcp_bildsprache.types import IdentityPack, IdentitySlot

logger = logging.getLogger(__name__)


# Markers that indicate a prompt is NOT about a person/scene. When any of
# these substrings appear (case-insensitive) in the prompt, identity
# resolution returns an empty list — no references, no composition clause.
PERSON_EXCLUDING_MARKERS: tuple[str, ...] = (
    "icon",
    "flat illustration",
    "abstract pattern",
    "logo",
    "architectural detail",
    "svg",
)

# Slot names that the `include_dogs` override controls. Matches the
# `@casey.berlin` manifest shape. Declared here so the override remains
# deterministic even if a future manifest uses different primary keys.
DOG_SLOT_NAMES: tuple[str, ...] = ("fimme", "sien")


class _SlotSchema(BaseModel):
    files: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class _RulesSchema(BaseModel):
    always_include: list[str] = Field(default_factory=list)
    include_if_prompt_matches: dict[str, list[str]] = Field(default_factory=dict)
    exclude_if_prompt_matches: dict[str, list[str]] = Field(default_factory=dict)


class _ManifestSchema(BaseModel):
    version: int = 1
    slots: dict[str, _SlotSchema] = Field(default_factory=dict)
    rules: _RulesSchema = Field(default_factory=_RulesSchema)


def _brand_context_for_dir(dir_name: str) -> str:
    """Map a directory name (e.g. "casey-berlin") back to a brand context
    string (e.g. "@casey.berlin"). Falls through to "@<dir_name>" if the
    directory does not appear in BRAND_PREFIXES.
    """
    for ctx, prefix in BRAND_PREFIXES.items():
        if prefix == dir_name:
            # BRAND_PREFIXES keys are like "casey.berlin" / "cdit-works.de"
            # — prepend @ for the canonical brand context string.
            return f"@{ctx}"
    return f"@{dir_name}"


def _load_one_pack(brand_dir: Path) -> IdentityPack | None:
    """Load a single brand's manifest + validate referenced files.

    Emits WARN logs for missing/malformed manifests and missing files;
    returns None for unrecoverable cases (missing or unparseable manifest).
    """
    manifest_path = brand_dir / "manifest.json"
    brand_ctx = _brand_context_for_dir(brand_dir.name)

    if not manifest_path.is_file():
        logger.warning(
            "identity_manifest_missing path=%s brand=%s",
            manifest_path,
            brand_ctx,
        )
        return None

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "identity_manifest_unparseable path=%s error=%s",
            manifest_path,
            exc,
        )
        return None

    try:
        parsed = _ManifestSchema.model_validate(raw)
    except ValidationError as exc:
        logger.warning(
            "identity_manifest_invalid path=%s error=%s",
            manifest_path,
            exc,
        )
        return None

    # Preserve manifest declaration order for slots.
    slots: list[IdentitySlot] = []
    for slot_name, slot_data in parsed.slots.items():
        resolved_files: list[Path] = []
        unavailable = False
        for file_name in slot_data.files:
            file_path = brand_dir / file_name
            if not file_path.is_file():
                logger.warning(
                    "identity_file_missing brand=%s slot=%s path=%s",
                    brand_ctx,
                    slot_name,
                    file_path,
                )
                unavailable = True
                continue
            resolved_files.append(file_path)

        # A slot with no available files is marked unavailable — resolver
        # will silently skip it.
        if not resolved_files:
            unavailable = True

        slots.append(
            IdentitySlot(
                name=slot_name,
                files=tuple(resolved_files),
                tags=tuple(slot_data.tags),
                unavailable=unavailable,
            )
        )

    pack = IdentityPack(
        brand=brand_ctx,
        slots=tuple(slots),
        always_include=tuple(parsed.rules.always_include),
        include_if_prompt_matches={
            k: tuple(v) for k, v in parsed.rules.include_if_prompt_matches.items()
        },
        exclude_if_prompt_matches={
            k: tuple(v) for k, v in parsed.rules.exclude_if_prompt_matches.items()
        },
    )

    logger.info(
        "identity_pack_loaded=True brand=%s slots=%s",
        brand_ctx,
        [s.name for s in slots if not s.unavailable],
    )
    return pack


def load_identity_packs(root: Path) -> dict[str, IdentityPack]:
    """Scan ``root`` for per-brand identity manifests and return a mapping
    from brand context (e.g. ``"@casey.berlin"``) to the loaded pack.

    Missing root directory → empty mapping, no log (this is the
    "identity feature not configured" case). Missing/malformed per-brand
    manifests → WARN per issue, pack omitted from the result. Missing
    referenced image files → WARN per file, slot marked unavailable but
    pack still returned.
    """
    if not root.is_dir():
        return {}

    packs: dict[str, IdentityPack] = {}
    # Sorted for deterministic startup logs / test ordering.
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        pack = _load_one_pack(child)
        if pack is not None:
            packs[pack.brand] = pack
    return packs


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _prompt_has_person_excluding_marker(prompt: str) -> bool:
    """Return True when the prompt contains any person-excluding marker."""
    lowered = prompt.lower()
    return any(marker in lowered for marker in PERSON_EXCLUDING_MARKERS)


def _keyword_matches(prompt: str, keywords: tuple[str, ...]) -> bool:
    """Case-insensitive substring OR-match."""
    lowered = prompt.lower()
    return any(kw.lower() in lowered for kw in keywords)


def resolve_identity(pack: IdentityPack, prompt: str) -> list[Path]:
    """Return the ordered list of reference-image paths for ``prompt``.

    Pure function: same (pack, prompt) always returns the same list in the
    same order (manifest declaration order for slots, file order within).

    Rules, in order:
    1. If the prompt contains a person-excluding marker, return [].
    2. Every slot in ``always_include`` is attached (if available).
    3. Slots with an ``include_if_prompt_matches`` entry are attached when
       a keyword matches AND no keyword in their ``exclude_if_prompt_matches``
       entry matches.
    4. Unavailable slots (missing files) are silently skipped.
    """
    if _prompt_has_person_excluding_marker(prompt):
        return []

    selected: list[Path] = []
    for slot in pack.slots:
        if slot.unavailable:
            continue

        # Exclude rule takes priority over everything — if it matches, skip.
        exclude_kws = pack.exclude_if_prompt_matches.get(slot.name, ())
        if exclude_kws and _keyword_matches(prompt, exclude_kws):
            continue

        included = False
        if slot.name in pack.always_include:
            included = True
        else:
            include_kws = pack.include_if_prompt_matches.get(slot.name, ())
            if include_kws and _keyword_matches(prompt, include_kws):
                included = True

        if included:
            selected.extend(slot.files)

    return selected


def resolve_identity_for_call(
    pack: IdentityPack,
    prompt: str,
    include_dogs: bool | None = None,
) -> list[Path]:
    """Wrapper around :func:`resolve_identity` that honours the
    ``include_dogs`` override.

    - ``None`` → use the manifest heuristic (default).
    - ``True`` → force-include the dog slots (``DOG_SLOT_NAMES``), even if
      the prompt does not match include keywords. Person-excluding markers
      still win (no dogs in an icon prompt).
    - ``False`` → suppress the dog slots, even if the prompt matches
      include keywords.
    """
    if include_dogs is None:
        return resolve_identity(pack, prompt)

    # Start from the heuristic result; we mutate it per the override.
    base = resolve_identity(pack, prompt)

    # Short-circuit: if the prompt was person-excluding, stay empty.
    if not base and _prompt_has_person_excluding_marker(prompt):
        return []

    dog_slots = [s for s in pack.slots if s.name in DOG_SLOT_NAMES and not s.unavailable]
    dog_files_set: set[Path] = set()
    for slot in dog_slots:
        dog_files_set.update(slot.files)

    if include_dogs is False:
        return [p for p in base if p not in dog_files_set]

    # include_dogs is True — ensure every available dog slot is present,
    # in manifest declaration order. Re-walk the slot list so ordering is
    # deterministic and matches the non-override behaviour.
    result: list[Path] = []
    base_set = set(base)
    for slot in pack.slots:
        if slot.unavailable:
            continue
        if slot.name in DOG_SLOT_NAMES:
            result.extend(slot.files)
        elif any(p in base_set for p in slot.files):
            result.extend(slot.files)
    return result


# ---------------------------------------------------------------------------
# Module-level cache (populated at startup)
# ---------------------------------------------------------------------------


_PACKS: dict[str, IdentityPack] = {}


def set_loaded_packs(packs: dict[str, IdentityPack]) -> None:
    """Replace the module-level identity-pack cache. Called from ``server.py``
    at startup once configuration is resolved.
    """
    global _PACKS
    _PACKS = dict(packs)


def get_loaded_packs() -> dict[str, IdentityPack]:
    """Return the currently-loaded identity packs (may be empty)."""
    return _PACKS


def get_pack_for_context(context: str | None) -> IdentityPack | None:
    """Look up an identity pack for a brand context (e.g. "@casey.berlin").

    Returns ``None`` when the context is falsy, unknown, or has no pack.
    """
    if not context:
        return None
    return _PACKS.get(context)
