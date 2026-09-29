"""Tests for identity pack loading and resolution."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from PIL import Image

from mcp_bildsprache.identity import (
    load_identity_packs,
    people_hint,
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
            "dog-1": {"files": ["dog-1-1.webp"], "tags": ["dog"]},
            "dog-2": {"files": ["dog-2-1.webp"], "tags": ["dog"]},
        },
        "rules": {
            "always_include": ["casey"],
            "include_if_prompt_matches": {
                "dog-1": ["walk", "outside", "forest", "morning", "personal"],
                "dog-2": ["walk", "outside", "forest", "morning", "personal"],
            },
            "exclude_if_prompt_matches": {
                "dog-1": ["client", "office", "meeting"],
                "dog-2": ["client", "office", "meeting"],
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
        # Post-collapse canonical: directory named 'casey' → key '@casey'.
        _write_pack(tmp_path, "casey", _default_casey_manifest())
        packs = load_identity_packs(tmp_path)
        assert set(packs.keys()) == {"@casey"}
        pack = packs["@casey"]
        assert isinstance(pack, IdentityPack)
        assert [s.name for s in pack.slots] == ["casey", "dog-1", "dog-2"]
        assert pack.always_include == ("casey",)

    def test_legacy_casey_berlin_directory_still_loads(self, tmp_path: Path):
        # Pre-rename production state: directory still named 'casey-berlin'.
        # The loader must still produce a usable pack so deploy ordering
        # (rename volume after stack restart) doesn't break.
        _write_pack(tmp_path, "casey-berlin", _default_casey_manifest())
        packs = load_identity_packs(tmp_path)
        # Pre-rename: the loader emits '@casey-berlin' (fall-through path
        # since the new BRAND_PREFIXES no longer maps any key to the
        # 'casey-berlin' directory name).
        assert "@casey-berlin" in packs

    def test_missing_manifest_warns(self, tmp_path: Path, caplog):
        (tmp_path / "casey").mkdir()
        with caplog.at_level(logging.WARNING, logger="mcp_bildsprache.identity"):
            packs = load_identity_packs(tmp_path)
        assert packs == {}
        assert any("identity_manifest_missing" in r.message for r in caplog.records)

    def test_malformed_manifest_warns(self, tmp_path: Path, caplog):
        pack_dir = tmp_path / "casey"
        pack_dir.mkdir()
        (pack_dir / "manifest.json").write_text("{not-json")
        with caplog.at_level(logging.WARNING, logger="mcp_bildsprache.identity"):
            packs = load_identity_packs(tmp_path)
        assert packs == {}
        assert any("identity_manifest_unparseable" in r.message for r in caplog.records)

    def test_missing_file_warns_and_marks_slot_unavailable(self, tmp_path: Path, caplog):
        manifest = _default_casey_manifest()
        pack_dir = tmp_path / "casey"
        pack_dir.mkdir()
        (pack_dir / "manifest.json").write_text(json.dumps(manifest))
        _write_tiny_webp(pack_dir / "casey-1.webp")
        _write_tiny_webp(pack_dir / "dog-2-1.webp")
        # dog-1-1.webp not created

        with caplog.at_level(logging.WARNING, logger="mcp_bildsprache.identity"):
            packs = load_identity_packs(tmp_path)

        assert "@casey" in packs
        pack = packs["@casey"]
        dog1 = next(s for s in pack.slots if s.name == "dog-1")
        assert dog1.unavailable is True
        assert any("identity_file_missing" in r.message and "dog-1-1.webp" in r.message
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _make_pack(tmp_path: Path) -> IdentityPack:
    """Build an IdentityPack with all slots available."""
    _write_pack(tmp_path, "casey", _default_casey_manifest())
    return load_identity_packs(tmp_path)["@casey"]


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
        assert names == ["casey-1.webp", "dog-1-1.webp", "dog-2-1.webp"]

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
        # Build a pack with dog-1 unavailable.
        slots = (
            IdentitySlot(name="casey", files=(tmp_path / "casey-1.webp",)),
            IdentitySlot(name="dog-1", files=(), unavailable=True),
            IdentitySlot(name="dog-2", files=(tmp_path / "dog-2-1.webp",)),
        )
        pack = IdentityPack(
            brand="@casey",
            slots=slots,
            always_include=("casey",),
            include_if_prompt_matches={
                "dog-1": ("walk",),
                "dog-2": ("walk",),
            },
            exclude_if_prompt_matches={},
        )
        # Create the files that do exist so paths are well-formed (resolver
        # does not stat, but cleanup's easier this way).
        _write_tiny_webp(tmp_path / "casey-1.webp")
        _write_tiny_webp(tmp_path / "dog-2-1.webp")
        result = resolve_identity(pack, "morning walk in the park")
        assert [p.name for p in result] == ["casey-1.webp", "dog-2-1.webp"]


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
        assert names == ["casey-1.webp", "dog-1-1.webp", "dog-2-1.webp"]

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

    def test_dog_slots_come_from_manifest_tags(self, tmp_path: Path):
        manifest = {
            "version": 1,
            "slots": {
                "casey": {"files": ["casey-1.webp"], "tags": ["person"]},
                "rex": {"files": ["rex-1.webp"], "tags": ["dog"]},
                "friend": {"files": ["friend-1.webp"], "tags": ["person"]},
            },
            "rules": {"always_include": ["casey", "rex", "friend"]},
        }
        _write_pack(tmp_path, "casey", manifest)
        pack = next(iter(load_identity_packs(tmp_path).values()))
        assert [s.name for s in pack.slots if s.is_dog] == ["rex"]
        got = resolve_identity_for_call(pack, "at the desk", include_dogs=False)
        assert [p.name for p in got] == ["casey-1.webp", "friend-1.webp"]


class TestIncludePeople:
    STILL_LIFE = "A still life on a wooden workbench with tools and a lamp. No people."

    def _names(self, pack, prompt, **kw):
        return [p.name for p in resolve_identity_for_call(pack, prompt, **kw)]

    def test_still_life_drops_person_refs(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        assert self._names(pack, self.STILL_LIFE) == []

    def test_negations_drop_person_refs(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        for prompt in (
            "an empty room at dusk",
            "a desk without people",
            "Nobody on the street",
            "Werkbank, keine Personen",
            "Ein Flur ohne Menschen",
        ):
            assert self._names(pack, prompt) == [], prompt

    def test_negation_keeps_dogs(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        assert self._names(pack, "morning walk in the forest, no people") == [
            "dog-1-1.webp", "dog-2-1.webp"
        ]

    def test_person_words_attach_refs(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        for prompt in ("portrait of Casey", "a man at his desk", "he reads by the window"):
            assert self._names(pack, prompt) == ["casey-1.webp"], prompt

    def test_person_word_needs_word_boundary(self):
        assert people_hint("the manifest on a shelf")[0] is None
        assert people_hint("a theme of hierarchy")[0] is None

    def test_no_signal_keeps_manifest_rules(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        assert self._names(pack, "late afternoon coffee") == ["casey-1.webp"]

    def test_flags_override_heuristic(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        assert self._names(pack, self.STILL_LIFE, include_people=True) == ["casey-1.webp"]
        assert self._names(pack, "portrait of Casey", include_people=False) == []

    def test_true_does_not_override_person_excluding_marker(self, tmp_path: Path):
        pack = _make_pack(tmp_path)
        assert self._names(pack, "a flat icon of Casey", include_people=True) == []
