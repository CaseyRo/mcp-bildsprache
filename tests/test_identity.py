"""Tests for identity pack loading and resolution."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from PIL import Image

from mcp_bildsprache.identity import (
    DOG_SLOT_NAMES,
    load_identity_packs,
    resolve_identity,
    resolve_identity_for_call,
)
from mcp_bildsprache.types import IdentityPack, IdentitySlot


def _write_tiny_webp(path: Path) -> None:
    """Write a tiny WebP image to ``path`` — just enough for file presence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(200, 100, 50)).save(buf, format="WEBP")
    path.write_bytes(buf.getvalue())


def _default_casey_manifest() -> dict:
    return {
        "version": 1,
        "slots": {
            "casey": {"files": ["casey-1.webp"], "tags": ["person", "primary"]},
            "fimme": {"files": ["fimme-1.webp"], "tags": ["dog"]},
            "sien": {"files": ["sien-1.webp"], "tags": ["dog"]},
        },
        "rules": {
            "always_include": ["casey"],
            "include_if_prompt_matches": {
                "fimme": ["walk", "outside", "forest", "morning", "personal"],
                "sien": ["walk", "outside", "forest", "morning", "personal"],
            },
            "exclude_if_prompt_matches": {
                "fimme": ["client", "office", "meeting"],
                "sien": ["client", "office", "meeting"],
            },
        },
    }


def _write_pack(root: Path, brand_dir: str, manifest: dict) -> Path:
    """Write a brand pack under ``root`` and create every file named in the manifest."""
    pack_dir = root / brand_dir
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "manifest.json").write_text(json.dumps(manifest))
    for slot in manifest.get("slots", {}).values():
        for fname in slot.get("files", []):
            _write_tiny_webp(pack_dir / fname)
    return pack_dir


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class TestLoadIdentityPacks:
    def test_missing_root_returns_empty(self, tmp_path: Path):
        assert load_identity_packs(tmp_path / "nonexistent") == {}

    def test_valid_manifest_loads(self, tmp_path: Path):
        _write_pack(tmp_path, "casey-berlin", _default_casey_manifest())
        packs = load_identity_packs(tmp_path)
        assert set(packs.keys()) == {"@casey.berlin"}
        pack = packs["@casey.berlin"]
        assert isinstance(pack, IdentityPack)
        assert [s.name for s in pack.slots] == ["casey", "fimme", "sien"]
        assert pack.always_include == ("casey",)

    def test_missing_manifest_warns(self, tmp_path: Path, caplog):
        # Directory exists but no manifest.json inside.
        (tmp_path / "casey-berlin").mkdir()
        with caplog.at_level(logging.WARNING, logger="mcp_bildsprache.identity"):
            packs = load_identity_packs(tmp_path)
        assert packs == {}
        assert any("identity_manifest_missing" in r.message for r in caplog.records)

    def test_malformed_manifest_warns(self, tmp_path: Path, caplog):
        pack_dir = tmp_path / "casey-berlin"
        pack_dir.mkdir()
        (pack_dir / "manifest.json").write_text("{not-json")
        with caplog.at_level(logging.WARNING, logger="mcp_bildsprache.identity"):
            packs = load_identity_packs(tmp_path)
        assert packs == {}
        assert any("identity_manifest_unparseable" in r.message for r in caplog.records)

    def test_missing_file_warns_and_marks_slot_unavailable(self, tmp_path: Path, caplog):
        manifest = _default_casey_manifest()
        # Write pack but skip writing fimme-1.webp.
        pack_dir = tmp_path / "casey-berlin"
        pack_dir.mkdir()
        (pack_dir / "manifest.json").write_text(json.dumps(manifest))
        _write_tiny_webp(pack_dir / "casey-1.webp")
        _write_tiny_webp(pack_dir / "sien-1.webp")
        # fimme-1.webp not created

        with caplog.at_level(logging.WARNING, logger="mcp_bildsprache.identity"):
            packs = load_identity_packs(tmp_path)

        assert "@casey.berlin" in packs
        pack = packs["@casey.berlin"]
        fimme = next(s for s in pack.slots if s.name == "fimme")
        assert fimme.unavailable is True
        assert any("identity_file_missing" in r.message and "fimme-1.webp" in r.message
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _make_pack(tmp_path: Path) -> IdentityPack:
    """Build an IdentityPack with all slots available."""
    _write_pack(tmp_path, "casey-berlin", _default_casey_manifest())
    return load_identity_packs(tmp_path)["@casey.berlin"]


class TestResolveIdentity:
    def test_personal_prompt_returns_casey_only(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        result = resolve_identity(pack, "late afternoon coffee at my desk")
        assert len(result) == 1
        assert result[0].name == "casey-1.webp"

    def test_outdoor_prompt_returns_casey_and_dogs(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        result = resolve_identity(pack, "morning walk through the forest with the dogs")
        names = [p.name for p in result]
        assert names == ["casey-1.webp", "fimme-1.webp", "sien-1.webp"]

    def test_exclude_wins_over_include(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        # "walk" is an include keyword; "office"/"meeting"/"client" are excludes.
        result = resolve_identity(pack, "walking to a client meeting in the office building")
        names = [p.name for p in result]
        assert names == ["casey-1.webp"]

    def test_person_excluding_marker_returns_empty(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        assert resolve_identity(pack, "a flat icon of a coffee cup") == []
        assert resolve_identity(pack, "abstract pattern of shapes") == []
        assert resolve_identity(pack, "logo for a company") == []
        assert resolve_identity(pack, "flat illustration of buildings") == []
        assert resolve_identity(pack, "an architectural detail") == []
        assert resolve_identity(pack, "svg of a tree") == []

    def test_case_insensitive_substring_match(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        result = resolve_identity(pack, "Morning Walking in the Forest")
        assert len(result) == 3  # casey + 2 dogs

    def test_deterministic_order(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        prompt = "morning walk in the park"
        assert resolve_identity(pack, prompt) == resolve_identity(pack, prompt)

    def test_unavailable_slot_skipped(self, tmp_path: Path):
        # Build a pack with fimme unavailable.
        slots = (
            IdentitySlot(name="casey", files=(tmp_path / "casey-1.webp",)),
            IdentitySlot(name="fimme", files=(), unavailable=True),
            IdentitySlot(name="sien", files=(tmp_path / "sien-1.webp",)),
        )
        pack = IdentityPack(
            brand="@casey.berlin",
            slots=slots,
            always_include=("casey",),
            include_if_prompt_matches={
                "fimme": ("walk",),
                "sien": ("walk",),
            },
            exclude_if_prompt_matches={},
        )
        # Create the files that do exist so paths are well-formed (resolver
        # does not stat, but cleanup's easier this way).
        _write_tiny_webp(tmp_path / "casey-1.webp")
        _write_tiny_webp(tmp_path / "sien-1.webp")
        result = resolve_identity(pack, "morning walk in the park")
        assert [p.name for p in result] == ["casey-1.webp", "sien-1.webp"]


class TestResolveIdentityForCall:
    def test_none_uses_heuristic(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        got = resolve_identity_for_call(pack, "morning walk", include_dogs=None)
        assert len(got) == 3

    def test_true_forces_dogs_even_without_keywords(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        got = resolve_identity_for_call(
            pack, "late afternoon coffee at my desk", include_dogs=True
        )
        names = [p.name for p in got]
        assert names == ["casey-1.webp", "fimme-1.webp", "sien-1.webp"]

    def test_false_suppresses_dogs_even_with_keywords(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        got = resolve_identity_for_call(
            pack, "morning walk through the forest", include_dogs=False
        )
        names = [p.name for p in got]
        assert names == ["casey-1.webp"]

    def test_true_does_not_override_person_excluding_marker(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        assert resolve_identity_for_call(
            pack, "a flat icon of a coffee cup", include_dogs=True
        ) == []

    def test_dog_slot_names_constant(self):
        assert set(DOG_SLOT_NAMES) == {"fimme", "sien"}
