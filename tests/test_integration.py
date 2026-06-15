"""Integration tests for generate_image end-to-end with hosting pipeline."""

import asyncio
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from mcp_bildsprache.storage import StorageError
from mcp_bildsprache.types import ProviderResult


def _fake_provider_result(model: str = "gpt-image-2") -> ProviderResult:
    buf = io.BytesIO()
    Image.new("RGB", (1024, 1024), color=(80, 120, 160)).save(buf, format="PNG")
    return ProviderResult(
        image_data=buf.getvalue(),
        mime_type="image/png",
        model=model,
        cost_estimate="$0.05",
    )


@pytest.fixture
def mock_provider():
    """Mock all providers to return a fake ProviderResult.

    Post-May-2026 collapse: openai + gemini are the active dispatch
    targets. flux/recraft modules stay in-tree but unreachable; we still
    mock them so any accidental dispatch doesn't hit the network.
    """
    result = _fake_provider_result()
    mock = AsyncMock(return_value=result)
    with patch(
        "mcp_bildsprache.server.PROVIDERS",
        {"openai": mock, "gemini": mock, "flux": mock, "recraft": mock},
    ):
        yield mock


class TestGenerateImageHosting:
    @pytest.mark.anyio
    async def test_hosting_enabled_returns_hosted_url(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="a beautiful sunset",
                context="casey",
                register="personal",
                platform="blog-hero",
            )

        assert "hosted_url" in result
        # Post-collapse: casey/ is the new prefix.
        assert result["hosted_url"].startswith("https://img.cdit-works.de/casey/")
        assert result["hosted_url"].endswith(".webp")
        assert result["dimensions"] == "1600x900"
        # Default raster path is OpenAI gpt-image-2.
        assert result["model"] == "gpt-image-2"
        assert "image_base64" not in result  # No raw data by default

        # Verify file was written
        webp_files = list(tmp_path.rglob("*.webp"))
        assert len(webp_files) == 1

        # Verify image dimensions are exact
        img = Image.open(webp_files[0])
        assert img.size == (1600, 900)

        # Verify sidecar
        json_files = list(tmp_path.rglob("*.json"))
        assert len(json_files) == 1
        sidecar = json.loads(json_files[0].read_text())
        assert sidecar["brand_context"] == "casey"

    @pytest.mark.anyio
    async def test_always_returns_hosted_url(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(prompt="test", dimensions="512x512")

        assert "hosted_url" in result
        assert result["response_mode"] == "url"
        assert "image_base64" not in result


class TestRawMode:
    @pytest.mark.anyio
    async def test_raw_returns_url_not_base64(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="test raw mode",
                dimensions="800x600",
                raw=True,
            )

        assert "hosted_url" in result
        assert "raw_url" in result
        assert result["raw_url"].startswith("https://img.cdit-works.de/")
        assert result["raw_url"].endswith("-raw.png")  # Original format, not WebP
        assert result["raw_mime_type"] == "image/png"
        assert "raw_image_base64" not in result  # No base64

        # Verify raw file was actually written
        raw_files = list(tmp_path.rglob("*-raw.png"))
        assert len(raw_files) == 1

    @pytest.mark.anyio
    async def test_raw_false_excludes_raw(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="test no raw",
                dimensions="800x600",
                raw=False,
            )

        assert "hosted_url" in result
        assert "raw_url" not in result


class TestNoFallback:
    @pytest.mark.anyio
    async def test_openai_failure_propagates_no_silent_swap(self, tmp_path: Path):
        """User directive 2026-05-09: 'It MUST work, no fallback!'

        When OpenAI fails, the error must propagate to the caller — we no
        longer silently swap to Gemini, which masked real OpenAI bugs
        (e.g. wrong size constraints for gpt-image-1-mini).
        """
        failing_mock = AsyncMock(side_effect=RuntimeError("OpenAI down"))
        success_mock = AsyncMock(return_value=_fake_provider_result())

        providers = {"openai": failing_mock, "gemini": success_mock}

        with patch("mcp_bildsprache.server.PROVIDERS", providers), \
             patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            from mcp_bildsprache.server import generate_image
            with pytest.raises(RuntimeError, match="OpenAI down"):
                await generate_image(prompt="test no fallback", dimensions="512x512")

        # Gemini must NOT have been called — no silent swap.
        success_mock.assert_not_awaited()

    @pytest.mark.anyio
    async def test_storage_error_propagates(self, tmp_path: Path, mock_provider):
        """StorageError should propagate — no base64 fallback."""
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.server.store_image", side_effect=StorageError("disk full")):
            with pytest.raises(StorageError, match="disk full"):
                await generate_image(prompt="test storage fail", dimensions="512x512")


class TestDimensionHandling:
    @pytest.mark.anyio
    async def test_explicit_dimensions_override_platform(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="test dimensions",
                platform="blog-hero",      # Would be 1600x900
                dimensions="800x600",       # Should override
            )

        assert result["dimensions"] == "800x600"
        webp_files = list(tmp_path.rglob("*.webp"))
        assert len(webp_files) == 1
        img = Image.open(webp_files[0])
        assert img.size == (800, 600)

    @pytest.mark.anyio
    async def test_default_dimensions_1200x1200(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings"), \
             patch("mcp_bildsprache.storage.settings") as ss:
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(prompt="test default size")

        assert result["dimensions"] == "1200x1200"


class TestIdentityIntegration:
    """End-to-end generate_image with a stubbed identity pack — asserts
    that reference bytes, composition clause, routing, and structured log
    all thread together correctly.
    """

    @staticmethod
    def _write_identity_pack(tmp_path: Path) -> Path:
        identity_root = tmp_path / "identity"
        pack_dir = identity_root / "casey-berlin"
        pack_dir.mkdir(parents=True)

        # Tiny files, written to disk so the resolver picks them up.
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), color=(100, 100, 100)).save(buf, format="WEBP")
        (pack_dir / "casey-1.webp").write_bytes(buf.getvalue())
        (pack_dir / "fimme-1.webp").write_bytes(buf.getvalue())
        (pack_dir / "sien-1.webp").write_bytes(buf.getvalue())

        manifest = {
            "version": 1,
            "slots": {
                "casey": {"files": ["casey-1.webp"], "tags": ["person"]},
                "fimme": {"files": ["fimme-1.webp"], "tags": ["dog"]},
                "sien":  {"files": ["sien-1.webp"],  "tags": ["dog"]},
            },
            "rules": {
                "always_include": ["casey"],
                "include_if_prompt_matches": {
                    "fimme": ["walk", "morning"],
                    "sien":  ["walk", "morning"],
                },
                "exclude_if_prompt_matches": {
                    "fimme": ["office", "meeting"],
                    "sien":  ["office", "meeting"],
                },
            },
        }
        (pack_dir / "manifest.json").write_text(json.dumps(manifest))
        return identity_root

    @pytest.mark.anyio
    async def test_personal_prompt_enhanced_prompt_and_log(
        self, tmp_path: Path, mock_provider, caplog
    ):
        import logging

        from mcp_bildsprache.identity import load_identity_packs, set_loaded_packs
        from mcp_bildsprache.presets import CASEY_COMPOSITION_CLAUSE
        from mcp_bildsprache.server import generate_image

        identity_root = self._write_identity_pack(tmp_path)
        set_loaded_packs(load_identity_packs(identity_root))

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path / "out")
            ss.image_domain = "https://img.cdit-works.de"

            with caplog.at_level(logging.INFO, logger="mcp_bildsprache.server"):
                await generate_image(
                    prompt="morning walk through the forest",
                    context="@casey.berlin",
                    dimensions="512x512",
                )

        # The provider mock was called with reference_images populated.
        call_kwargs = mock_provider.await_args.kwargs
        assert "reference_images" in call_kwargs
        refs = call_kwargs["reference_images"]
        assert len(refs) == 3

        # The enhanced prompt includes the composition clause.
        call_args = mock_provider.await_args.args
        sent_prompt = call_args[0]
        assert CASEY_COMPOSITION_CLAUSE in sent_prompt

        # An INFO record with identity_resolved fields is emitted.
        rec = next(r for r in caplog.records if "identity_resolved" in r.message)
        assert "brand=@casey.berlin" in rec.message
        assert "slots=['casey', 'fimme', 'sien']" in rec.message
        assert "has_include_dogs_override=False" in rec.message

        # Cleanup — avoid leaking into other tests.
        set_loaded_packs({})

    @pytest.mark.anyio
    async def test_person_excluding_prompt_skips_identity_and_clause(
        self, tmp_path: Path, mock_provider
    ):
        from mcp_bildsprache.identity import load_identity_packs, set_loaded_packs
        from mcp_bildsprache.presets import CASEY_COMPOSITION_CLAUSE
        from mcp_bildsprache.server import generate_image

        identity_root = self._write_identity_pack(tmp_path)
        set_loaded_packs(load_identity_packs(identity_root))

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path / "out")
            ss.image_domain = "https://img.cdit-works.de"

            await generate_image(
                prompt="a flat icon of a coffee cup",
                context="@casey.berlin",
                dimensions="512x512",
            )

        # No reference images forwarded; no composition clause in prompt.
        call_kwargs = mock_provider.await_args.kwargs
        assert "reference_images" not in call_kwargs
        sent_prompt = mock_provider.await_args.args[0]
        assert CASEY_COMPOSITION_CLAUSE not in sent_prompt

        set_loaded_packs({})

    @pytest.mark.anyio
    async def test_composition_clause_scoped_to_casey_only(
        self, tmp_path: Path, mock_provider
    ):
        """Yorizon must never get the casey composition clause, even
        when an identity pack happens to be loaded for casey."""
        from mcp_bildsprache.identity import load_identity_packs, set_loaded_packs
        from mcp_bildsprache.presets import CASEY_COMPOSITION_CLAUSE
        from mcp_bildsprache.server import generate_image

        identity_root = self._write_identity_pack(tmp_path)
        set_loaded_packs(load_identity_packs(identity_root))

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path / "out")
            ss.image_domain = "https://img.cdit-works.de"

            await generate_image(
                prompt="morning walk",
                context="yorizon",
                dimensions="512x512",
            )

        sent_prompt = mock_provider.await_args.args[0]
        assert CASEY_COMPOSITION_CLAUSE not in sent_prompt

        set_loaded_packs({})

    @pytest.mark.anyio
    async def test_caller_supplied_refs_bypass_resolver(
        self, tmp_path: Path, mock_provider
    ):
        from mcp_bildsprache.identity import load_identity_packs, set_loaded_packs
        from mcp_bildsprache.server import generate_image

        identity_root = self._write_identity_pack(tmp_path)
        set_loaded_packs(load_identity_packs(identity_root))

        caller_ref = b"caller-supplied-bytes-x"

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path / "out")
            ss.image_domain = "https://img.cdit-works.de"

            await generate_image(
                prompt="morning walk",
                context="@casey.berlin",
                dimensions="512x512",
                reference_images=[caller_ref],
            )

        call_kwargs = mock_provider.await_args.kwargs
        refs = call_kwargs["reference_images"]
        assert len(refs) == 1
        assert refs[0] == caller_ref

        set_loaded_packs({})


class TestStaticMountHygiene:
    """Reference images on /data/identity must never be reachable via the
    public static mount. This guards against a regression where someone
    changes the mount root to /data/ (both subdirs would be exposed).
    """

    def test_mount_target_is_images_not_identity(self, tmp_path: Path):
        """_mount_static_files mounts the image_storage_path directory only."""
        from unittest.mock import MagicMock

        from mcp_bildsprache.server import _mount_static_files

        images_dir = tmp_path / "images"
        identity_dir = tmp_path / "identity"
        images_dir.mkdir()
        identity_dir.mkdir()
        (identity_dir / "secret.webp").write_bytes(b"not-public")

        fake_app = MagicMock()
        with patch("mcp_bildsprache.server.settings") as s:
            s.image_storage_path = str(images_dir)
            _mount_static_files(fake_app)

        # Exactly one mount call, rooted at the images dir.
        assert fake_app.mount.call_count == 1
        mounted = fake_app.mount.call_args.args[1]
        # StaticFiles(directory=...) — inspect the directory attribute.
        assert str(images_dir) == mounted.directory
        # The identity directory must NOT be inside the mounted root.
        assert not mounted.directory.startswith(str(identity_dir))
        assert "identity" not in mounted.directory


class TestOtherTools:
    @pytest.mark.anyio
    async def test_generate_prompt_basic(self):
        from mcp_bildsprache.server import generate_prompt

        result = await generate_prompt(
            prompt="a sunset over Berlin",
            context="casey",
            register="personal",
            platform="blog-hero",
        )

        assert "engineered_prompt" in result
        assert "a sunset over Berlin" in result["engineered_prompt"]
        # Default raster path is OpenAI gpt-image-2.
        assert result["model"] == "openai"
        assert result["dimensions"] == "1600x900"
        assert result["brand_context"] == "casey"
        assert result["register"] == "personal"

    @pytest.mark.anyio
    async def test_list_models_returns_entries_when_keys_set(self):
        from mcp_bildsprache.server import list_models

        with patch("mcp_bildsprache.server.settings") as s:
            from pydantic import SecretStr
            s.openai_api_key = SecretStr("fake-key")
            s.gemini_api_key = SecretStr("fake-key")
            s.bfl_api_key = SecretStr("fake-key")
            s.recraft_api_key = SecretStr("fake-key")
            s.openai_image_model = "gpt-image-2"
            s.openai_image_model_draft = "gpt-image-1-mini"

            result = await list_models()

        assert "providers" in result
        providers = result["providers"]
        # Active providers post-collapse: openai + gemini.
        ids = {m["id"] for m in providers}
        assert ids == {"openai", "gemini"}

        # Disabled providers reported separately.
        assert "disabled_providers" in result
        disabled_ids = {p["provider"] for p in result["disabled_providers"]}
        assert disabled_ids == {"bfl", "recraft"}

        assert "identity_packs" in result
        assert isinstance(result["identity_packs"], dict)
        # New: diagram-capable advertisement.
        assert result["diagram_capable"] == ["openai", "gemini"]
        assert set(result["diagram_formats"]) == {"flow", "sequence", "state"}

        # Model lineup refresh (CDI-1264): the openai provider advertises
        # gpt-image-1.5 (high) alongside gpt-image-2; gemini advertises Nano
        # Banana Pro + Nano Banana 2 and NO longer gemini-2.5-flash-image.
        by_id = {m["id"]: m for m in providers}
        assert "gpt-image-1.5" in by_id["openai"]["models"]
        assert "gpt-image-2" in by_id["openai"]["models"]
        assert "gemini-3-pro-image-preview" in by_id["gemini"]["models"]
        assert "gemini-3.1-flash-image-preview" in by_id["gemini"]["models"]
        assert "gemini-2.5-flash-image" not in by_id["gemini"]["models"]
        assert by_id["gemini"]["default"] == "gemini-3-pro-image-preview"

    @pytest.mark.anyio
    async def test_get_visual_presets_returns_presets(self):
        from mcp_bildsprache.server import get_visual_presets

        result = await get_visual_presets()
        assert "presets" in result
        assert "platforms" in result
        # Active brand list post-collapse.
        assert "casey" in result["presets"]
        assert "yorizon" in result["presets"]
        # Register overlays surfaced separately.
        assert "casey_register_overlays" in result
        assert set(result["casey_register_overlays"].keys()) == {
            "personal",
            "professional",
        }

    @pytest.mark.anyio
    async def test_get_visual_presets_specific_context(self):
        from mcp_bildsprache.server import get_visual_presets

        result = await get_visual_presets(context="casey", register="personal")
        assert "context" in result
        assert "preset" in result
        assert "Register: personal" in result["preset"]
        assert "paper bone" in result["preset"].lower() or "#F4EFE3" in result["preset"]


# ---------------------------------------------------------------------------
# list_recent_generations (CDI-1253 — recover timed-out-but-completed renders)
# ---------------------------------------------------------------------------


def _seed_generation(
    root: Path,
    brand: str,
    slug: str,
    *,
    width: int = 1200,
    height: int = 1200,
    prompt: str = "a prompt",
    created_at: str | None = None,
    domain: str = "https://img.cdit-works.de",
) -> None:
    """Write a webp + sidecar pair the same shape store_image produces."""
    d = root / brand
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{slug}-{width}x{height}"
    (d / f"{stem}.webp").write_bytes(b"fakewebpbytes")
    body = {
        "prompt": prompt,
        "model": "gpt-image-2",
        "cost_estimate": "$0.05",
        "dimensions": f"{width}x{height}",
        "hosted_url": f"{domain}/{brand}/{stem}.webp",
    }
    if created_at is not None:
        body["generated_at"] = created_at
    (d / f"{stem}.json").write_text(json.dumps(body))


class TestListRecentGenerations:
    @pytest.mark.anyio
    async def test_returns_newest_first_with_urls(self, tmp_path: Path):
        from mcp_bildsprache.server import list_recent_generations

        _seed_generation(
            tmp_path, "casey", "old", prompt="oldest",
            created_at="2026-01-01T00:00:00+00:00",
        )
        _seed_generation(
            tmp_path, "casey", "new", prompt="newest",
            created_at="2026-06-01T00:00:00+00:00",
        )

        with patch("mcp_bildsprache.server.settings") as s:
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            result = await list_recent_generations()

        assert result["total"] == 2
        assert result["returned"] == 2
        gens = result["generations"]
        # Newest first.
        assert gens[0]["prompt"] == "newest"
        assert gens[1]["prompt"] == "oldest"
        # Hosted URL is recoverable for each (the CDI-1253 fix).
        assert gens[0]["hosted_url"].startswith("https://img.cdit-works.de/casey/")
        assert gens[0]["hosted_url"].endswith(".webp")
        assert gens[0]["created_at"].startswith("2026-06-01")

    @pytest.mark.anyio
    async def test_brand_filter(self, tmp_path: Path):
        from mcp_bildsprache.server import list_recent_generations

        _seed_generation(tmp_path, "casey", "a")
        _seed_generation(tmp_path, "yorizon", "b")

        with patch("mcp_bildsprache.server.settings") as s:
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            result = await list_recent_generations(brand="yorizon")

        assert result["total"] == 1
        assert result["brand"] == "yorizon"
        assert all(g["brand"] == "yorizon" for g in result["generations"])

    @pytest.mark.anyio
    async def test_legacy_brand_alias_matches_dir(self, tmp_path: Path):
        from mcp_bildsprache.server import list_recent_generations

        # Legacy 'casey-berlin' directory still serves historical URLs; a
        # caller asking for that key should match the verbatim dir.
        _seed_generation(tmp_path, "casey-berlin", "legacy")

        with patch("mcp_bildsprache.server.settings") as s:
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            result = await list_recent_generations(brand="casey-berlin")

        assert result["total"] == 1
        assert result["generations"][0]["brand"] == "casey-berlin"

    @pytest.mark.anyio
    async def test_limit_and_offset(self, tmp_path: Path):
        from mcp_bildsprache.server import list_recent_generations

        for i in range(5):
            _seed_generation(
                tmp_path, "casey", f"img{i}", prompt=f"p{i}",
                created_at=f"2026-06-0{i + 1}T00:00:00+00:00",
            )

        with patch("mcp_bildsprache.server.settings") as s:
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            page1 = await list_recent_generations(limit=2, offset=0)
            page2 = await list_recent_generations(limit=2, offset=2)

        assert page1["total"] == 5
        assert page1["returned"] == 2
        assert page1["limit"] == 2
        # Newest first → p4, p3 on the first page; p2, p1 on the second.
        assert [g["prompt"] for g in page1["generations"]] == ["p4", "p3"]
        assert [g["prompt"] for g in page2["generations"]] == ["p2", "p1"]

    @pytest.mark.anyio
    async def test_empty_dir_returns_clean_empty(self, tmp_path: Path):
        """Unhappy path: no generations → clean empty result, not an error."""
        from mcp_bildsprache.server import list_recent_generations

        with patch("mcp_bildsprache.server.settings") as s:
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            result = await list_recent_generations(brand="casey")

        assert "error" not in result
        assert result["total"] == 0
        assert result["returned"] == 0
        assert result["generations"] == []

    @pytest.mark.anyio
    async def test_limit_zero_reports_total_but_no_page(self, tmp_path: Path):
        """Unhappy path: limit=0 returns no items but still reports total."""
        from mcp_bildsprache.server import list_recent_generations

        _seed_generation(tmp_path, "casey", "a")

        with patch("mcp_bildsprache.server.settings") as s:
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            result = await list_recent_generations(limit=0)

        assert result["total"] == 1
        assert result["returned"] == 0
        assert result["limit"] == 0
        assert result["generations"] == []


# ---------------------------------------------------------------------------
# Progress / log notification guard (CDI-1253 — closed streamable-HTTP session)
# ---------------------------------------------------------------------------


class TestProgressLogGuard:
    @pytest.mark.anyio
    async def test_progress_swallows_closed_stream(self):
        """A closed-session write must NOT propagate out of _progress —
        otherwise it tears down the still-running tool call (the -32001 bug).
        """
        import anyio

        from mcp_bildsprache.server import _progress

        ctx = AsyncMock()
        ctx.report_progress.side_effect = anyio.ClosedResourceError()

        # Must return None without raising.
        assert await _progress(ctx, 1, 5, "step") is None
        ctx.report_progress.assert_awaited_once()

    @pytest.mark.anyio
    async def test_info_swallows_broken_stream(self):
        import anyio

        from mcp_bildsprache.server import _info

        ctx = AsyncMock()
        ctx.info.side_effect = anyio.BrokenResourceError()

        assert await _info(ctx, "hello") is None
        ctx.info.assert_awaited_once()

    @pytest.mark.anyio
    async def test_progress_swallows_generic_exception(self):
        from mcp_bildsprache.server import _progress

        ctx = AsyncMock()
        ctx.report_progress.side_effect = RuntimeError("boom")

        assert await _progress(ctx, 1, 5, "step") is None

    @pytest.mark.anyio
    async def test_generate_image_completes_despite_closed_session(
        self, tmp_path: Path, mock_provider
    ):
        """End-to-end: a session that dies on the FIRST progress write still
        produces a stored, indexed artifact recoverable via the index — proving
        the guard keeps the render alive instead of crashing the session.
        """
        import anyio

        from mcp_bildsprache.server import generate_image, list_recent_generations

        ctx = AsyncMock()
        # Every progress + info write fails as if the session timed out.
        ctx.report_progress.side_effect = anyio.ClosedResourceError()
        ctx.info.side_effect = anyio.ClosedResourceError()
        # No elicitation handler → _confirm_cost proceeds.
        ctx.elicit.side_effect = anyio.ClosedResourceError()

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="a resilient render",
                context="casey",
                platform="blog-hero",
                ctx=ctx,
            )

            # The render completed despite every notification write failing.
            assert result["hosted_url"].startswith("https://img.cdit-works.de/casey/")

            # And it's recoverable via the listing tool.
            recovered = await list_recent_generations()

        assert recovered["total"] == 1
        assert recovered["generations"][0]["hosted_url"] == result["hosted_url"]


# ---------------------------------------------------------------------------
# Async dispatch+poll (CDI-1266) — generate_image / get_image_result
# ---------------------------------------------------------------------------


def _slow_provider(delay: float = 5.0):
    """An AsyncMock-shaped provider whose render takes `delay` seconds.

    Used to force the inline-wait budget to expire so generate_image returns a
    {job_id, status: "pending"} handle while the (detached) render keeps going.
    """

    async def _render(*args, **kwargs):
        await asyncio.sleep(delay)
        return _fake_provider_result()

    return _render


class TestAsyncDispatchPoll:
    @staticmethod
    def _point_ledger_at(tmp_path: Path, monkeypatch) -> Path:
        from mcp_bildsprache.config import settings as cfg

        ledger_file = tmp_path / "_ledger" / "generations.jsonl"
        monkeypatch.setattr(cfg, "ledger_enabled", True)
        monkeypatch.setattr(cfg, "ledger_path", str(ledger_file))
        return ledger_file

    @pytest.fixture(autouse=True)
    def _fresh_registry(self, monkeypatch):
        from mcp_bildsprache import jobs

        reg = jobs.JobRegistry()
        monkeypatch.setattr(jobs, "_REGISTRY", reg)
        yield reg
        # Cancel any leftover detached render tasks (e.g. the deliberately-slow
        # 5s provider) so they don't outlive the test's event loop.
        for task in list(jobs._BACKGROUND_TASKS):
            task.cancel()

    @pytest.mark.anyio
    async def test_fast_render_returns_hosted_url_inline(
        self, tmp_path: Path, mock_provider
    ):
        """Backward compatible: a render that finishes within sync_wait_seconds
        returns the hosted_url inline exactly as before (no job handle)."""
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            s.sync_wait_seconds = 30  # plenty for the instant mock
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="fast", context="casey", dimensions="512x512"
            )

        assert "job_id" not in result
        assert result.get("status") != "pending"
        assert result["hosted_url"].startswith("https://img.cdit-works.de/casey/")
        assert result["response_mode"] == "url"

    @pytest.mark.anyio
    async def test_slow_render_returns_job_handle(
        self, tmp_path: Path, _fresh_registry
    ):
        """A render exceeding the inline budget returns {job_id, status:pending}
        within the budget — NOT the hosted_url."""
        slow = _slow_provider(delay=5.0)

        with patch(
            "mcp_bildsprache.server.PROVIDERS",
            {"openai": slow, "gemini": slow, "flux": slow, "recraft": slow},
        ), patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            s.sync_wait_seconds = 0.1  # tiny budget → handle returned fast
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            from mcp_bildsprache.server import generate_image

            result = await generate_image(
                prompt="slow", context="casey", dimensions="512x512"
            )

        assert result["status"] == "pending"
        assert result["poll_with"] == "get_image_result"
        assert "hosted_url" not in result
        assert result["job_id"]
        # The job is registered as pending right after dispatch.
        rec = _fresh_registry.get(result["job_id"])
        assert rec is not None and rec.status == "pending"

    @pytest.mark.anyio
    async def test_background_true_returns_handle_immediately(
        self, tmp_path: Path, mock_provider, _fresh_registry
    ):
        """background=True skips the inline wait → immediate job handle even for
        a fast render."""
        from mcp_bildsprache.server import generate_image

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            s.sync_wait_seconds = 30
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="bg", context="casey", dimensions="512x512", background=True
            )

        assert result["status"] == "pending"
        assert result["job_id"]

    @pytest.mark.anyio
    async def test_background_render_completes_and_writes_ledger_after_return(
        self, tmp_path: Path, _fresh_registry, monkeypatch
    ):
        """CORE RISK: the detached render COMPLETES, updates the registry, AND
        writes the CDI-1264 ledger line even though generate_image already
        returned a pending handle (the dispatching request is done)."""
        from mcp_bildsprache import ledger as ledmod
        from mcp_bildsprache.server import generate_image, get_image_result

        ledger_file = self._point_ledger_at(tmp_path, monkeypatch)
        slow = _slow_provider(delay=0.15)

        with patch(
            "mcp_bildsprache.server.PROVIDERS",
            {"openai": slow, "gemini": slow, "flux": slow, "recraft": slow},
        ), patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            s.sync_wait_seconds = 0.01  # return handle before render finishes
            s.poll_wait_max_seconds = 55
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            dispatched = await generate_image(
                prompt="detached", context="casey", dimensions="512x512"
            )
            assert dispatched["status"] == "pending"
            job_id = dispatched["job_id"]

            # The dispatching call has returned; the render is still running on a
            # detached task. Poll until it finishes (long-poll does the waiting).
            polled = await get_image_result(job_id, wait_seconds=5)

        assert polled["status"] == "done"
        assert polled["source"] == "registry"
        assert polled["hosted_url"].startswith("https://img.cdit-works.de/casey/")

        # The artifact was actually written to disk by the detached task.
        webp_files = list(tmp_path.rglob("*.webp"))
        assert len(webp_files) == 1

        # And the ledger line fired on the async path.
        recs = ledmod.read_records(path=ledger_file)
        assert len(recs) == 1
        assert recs[0]["outcome"] == "success"
        assert recs[0]["request_id"] == job_id
        assert recs[0]["delivery"] == "delivered"
        assert recs[0]["hosted_url"] == polled["hosted_url"]

    @pytest.mark.anyio
    async def test_get_image_result_pending_then_done(
        self, tmp_path: Path, _fresh_registry, monkeypatch
    ):
        """A single-shot poll (wait_seconds=0) returns pending while in flight,
        then done once the detached render completes."""
        self._point_ledger_at(tmp_path, monkeypatch)
        slow = _slow_provider(delay=0.2)

        with patch(
            "mcp_bildsprache.server.PROVIDERS",
            {"openai": slow, "gemini": slow, "flux": slow, "recraft": slow},
        ), patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            s.sync_wait_seconds = 0.01
            s.poll_wait_max_seconds = 55
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            from mcp_bildsprache.server import generate_image, get_image_result

            dispatched = await generate_image(
                prompt="poll me", context="casey", dimensions="512x512"
            )
            job_id = dispatched["job_id"]

            # Immediate single-shot poll → still pending.
            first = await get_image_result(job_id, wait_seconds=0)
            assert first["status"] == "pending"
            assert first["source"] == "registry"

            # Long-poll until done.
            second = await get_image_result(job_id, wait_seconds=5)

        assert second["status"] == "done"
        assert second["hosted_url"].startswith("https://img.cdit-works.de/casey/")

    @pytest.mark.anyio
    async def test_get_image_result_error_surfaced(
        self, tmp_path: Path, _fresh_registry, monkeypatch
    ):
        """A failed detached render surfaces status=error via the poll."""
        self._point_ledger_at(tmp_path, monkeypatch)

        async def _boom(*a, **k):
            await asyncio.sleep(0.05)
            raise RuntimeError("provider exploded")

        with patch(
            "mcp_bildsprache.server.PROVIDERS",
            {"openai": _boom, "gemini": _boom},
        ), patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            s.sync_wait_seconds = 0.01  # return handle before the boom
            s.poll_wait_max_seconds = 55
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            from mcp_bildsprache.server import generate_image, get_image_result

            dispatched = await generate_image(
                prompt="will fail", context="casey", dimensions="512x512"
            )
            job_id = dispatched["job_id"]
            polled = await get_image_result(job_id, wait_seconds=5)

        assert polled["status"] == "error"
        assert "provider exploded" in polled["error"]
        assert polled["error_category"] == "RuntimeError"

    @pytest.mark.anyio
    async def test_get_image_result_ledger_fallback_on_registry_miss(
        self, tmp_path: Path, _fresh_registry, monkeypatch
    ):
        """Durable fallback: when the in-process registry has no record (restart /
        other worker), a successful result is recovered from the ledger by its
        request_id == job_id."""
        from mcp_bildsprache import ledger as ledmod
        from mcp_bildsprache.server import get_image_result

        ledger_file = self._point_ledger_at(tmp_path, monkeypatch)

        # Seed a ledger record as if a prior process had rendered it. The
        # in-process registry (fresh per this test) does NOT know this job_id.
        ledmod.append_record(
            ledmod.build_record(
                request_id="orphan-job-1",
                outcome="success",
                model="gpt-image-2",
                provider="openai",
                brand="casey",
                width=1024,
                height=1024,
                hosted_url="https://img.cdit-works.de/casey/recovered-1024x1024.webp",
                latency_ms=51234,
                delivery="delivered",
            ),
            path=ledger_file,
        )

        with patch("mcp_bildsprache.server.settings") as s:
            s.poll_wait_max_seconds = 55
            result = await get_image_result("orphan-job-1")

        assert result["status"] == "done"
        assert result["source"] == "ledger"
        assert result["hosted_url"] == (
            "https://img.cdit-works.de/casey/recovered-1024x1024.webp"
        )
        assert result["model"] == "gpt-image-2"
        assert result["latency_ms"] == 51234

    @pytest.mark.anyio
    async def test_get_image_result_ledger_fallback_failure(
        self, tmp_path: Path, _fresh_registry, monkeypatch
    ):
        """A failure ledger record (registry miss) resolves to status=error."""
        from mcp_bildsprache import ledger as ledmod
        from mcp_bildsprache.server import get_image_result

        ledger_file = self._point_ledger_at(tmp_path, monkeypatch)
        ledmod.append_record(
            ledmod.build_record(
                request_id="orphan-fail-1",
                outcome="provider_error",
                model="gpt-image-2",
                provider="openai",
                brand="casey",
                width=1024,
                height=1024,
                error_category="HTTPStatusError",
                error_message="500 from provider",
            ),
            path=ledger_file,
        )

        with patch("mcp_bildsprache.server.settings") as s:
            s.poll_wait_max_seconds = 55
            result = await get_image_result("orphan-fail-1")

        assert result["status"] == "error"
        assert result["source"] == "ledger"
        assert "500 from provider" in result["error"]
        assert result["error_category"] == "HTTPStatusError"

    @pytest.mark.anyio
    async def test_get_image_result_not_found(
        self, tmp_path: Path, _fresh_registry, monkeypatch
    ):
        """Unknown to both registry and ledger → status=not_found (not an error)."""
        self._point_ledger_at(tmp_path, monkeypatch)
        from mcp_bildsprache.server import get_image_result

        with patch("mcp_bildsprache.server.settings") as s:
            s.poll_wait_max_seconds = 55
            result = await get_image_result("never-existed")

        assert result["status"] == "not_found"
        assert result["job_id"] == "never-existed"


# ---------------------------------------------------------------------------
# Generation outcome ledger wiring (CDI-1264)
# ---------------------------------------------------------------------------


class TestGenerationLedgerWiring:
    """One ledger line per attempt on the success AND failure paths, plus the
    success-vs-teardown convention and the generation_stats tool."""

    @staticmethod
    def _point_ledger_at(tmp_path: Path, monkeypatch) -> Path:
        """Route the (config-level) ledger at a tmp file and return its path."""
        from mcp_bildsprache.config import settings as cfg

        ledger_file = tmp_path / "_ledger" / "generations.jsonl"
        monkeypatch.setattr(cfg, "ledger_enabled", True)
        monkeypatch.setattr(cfg, "ledger_path", str(ledger_file))
        return ledger_file

    @pytest.mark.anyio
    async def test_success_writes_exactly_one_line(
        self, tmp_path: Path, mock_provider, monkeypatch
    ):
        from mcp_bildsprache import ledger as ledmod
        from mcp_bildsprache.server import generate_image

        ledger_file = self._point_ledger_at(tmp_path, monkeypatch)

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="ledger success", context="casey", dimensions="512x512"
            )

        assert result["hosted_url"].startswith("https://img.cdit-works.de/casey/")
        recs = ledmod.read_records(path=ledger_file)
        assert len(recs) == 1
        rec = recs[0]
        assert rec["outcome"] == "success"
        assert rec["model"] == "gpt-image-2"
        assert rec["brand"] == "casey"
        assert rec["delivery"] == "delivered"
        assert rec["requested_size"] == "512x512"
        assert "latency_ms" in rec
        assert rec["hosted_url"] == result["hosted_url"]

    @pytest.mark.anyio
    async def test_failure_writes_exactly_one_line(
        self, tmp_path: Path, monkeypatch
    ):
        from mcp_bildsprache import ledger as ledmod
        from mcp_bildsprache.server import generate_image

        ledger_file = self._point_ledger_at(tmp_path, monkeypatch)
        failing = AsyncMock(side_effect=RuntimeError("OpenAI 500 boom"))

        with patch("mcp_bildsprache.server.PROVIDERS", {"openai": failing, "gemini": failing}), \
             patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            with pytest.raises(RuntimeError, match="OpenAI 500 boom"):
                await generate_image(
                    prompt="ledger failure", context="casey", dimensions="512x512"
                )

        recs = ledmod.read_records(path=ledger_file)
        assert len(recs) == 1
        rec = recs[0]
        assert rec["outcome"] in ("provider_error", "other")
        assert rec["error_category"] == "RuntimeError"
        assert "OpenAI 500 boom" in rec["error_message"]
        assert rec["brand"] == "casey"
        assert "latency_ms" in rec

    @pytest.mark.anyio
    async def test_closed_stream_records_success_delivered_on_async_path(
        self, tmp_path: Path, mock_provider, monkeypatch
    ):
        """CDI-1266: the render now runs on a DETACHED background task with no
        stream to tear down, so a closed-stream on the dispatching request's
        inline-wait notification no longer taints delivery. The ledger record is
        honestly outcome=success + delivery=delivered (the render's own delivery
        to disk + registry succeeded); the caller recovers the URL by polling
        get_image_result / list_recent_generations.

        (Pre-CDI-1266 this recorded delivery=teardown_closed_stream because the
        render shared the request task; that coupling is gone.)
        """
        import anyio

        from mcp_bildsprache import ledger as ledmod
        from mcp_bildsprache.server import generate_image

        ledger_file = self._point_ledger_at(tmp_path, monkeypatch)

        ctx = AsyncMock()
        ctx.report_progress.side_effect = anyio.ClosedResourceError()
        ctx.info.side_effect = anyio.ClosedResourceError()
        ctx.elicit.side_effect = anyio.ClosedResourceError()

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            s.sync_wait_seconds = 30  # ample for the instant mock → inline result
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="torn render", context="casey", platform="blog-hero", ctx=ctx
            )

        assert result["hosted_url"].startswith("https://img.cdit-works.de/casey/")
        recs = ledmod.read_records(path=ledger_file)
        assert len(recs) == 1
        assert recs[0]["outcome"] == "success"
        assert recs[0]["delivery"] == "delivered"

    @pytest.mark.anyio
    async def test_ledger_write_failure_never_breaks_generation(
        self, tmp_path: Path, mock_provider, monkeypatch
    ):
        """Acceptance: a broken ledger append must not break the generation."""
        from mcp_bildsprache.server import generate_image

        # Point the ledger at an impossible path (parent is a file) so the
        # best-effort append fails internally.
        from mcp_bildsprache.config import settings as cfg

        blocker = tmp_path / "blocker"
        blocker.write_text("file-not-dir")
        monkeypatch.setattr(cfg, "ledger_enabled", True)
        monkeypatch.setattr(cfg, "ledger_path", str(blocker / "sub" / "led.jsonl"))

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            s.image_storage_path = str(tmp_path)
            s.image_domain = "https://img.cdit-works.de"
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_image(
                prompt="resilient", context="casey", dimensions="512x512"
            )

        # Generation still succeeded despite the ledger write failing.
        assert result["hosted_url"].startswith("https://img.cdit-works.de/casey/")

    @pytest.mark.anyio
    async def test_generation_stats_happy_path(self, tmp_path: Path, monkeypatch):
        from mcp_bildsprache import ledger as ledmod
        from mcp_bildsprache.server import generation_stats

        ledger_file = self._point_ledger_at(tmp_path, monkeypatch)
        for outcome in ["success", "success", "provider_error"]:
            ledmod.append_record(
                ledmod.build_record(
                    request_id=f"{outcome}-x",
                    outcome=outcome,
                    model="gpt-image-2",
                    provider="openai",
                    brand="casey",
                    width=1024,
                    height=1024,
                ),
                path=ledger_file,
            )

        stats = await generation_stats(days=30)
        assert "error" not in stats
        assert stats["totals"]["attempts"] == 3
        assert stats["totals"]["successes"] == 2
        by_model = {m["model"]: m for m in stats["by_model"]}
        assert by_model["gpt-image-2"]["success_pct"] == 66.67

    @pytest.mark.anyio
    async def test_generation_stats_empty_returns_clean_zeros(
        self, tmp_path: Path, monkeypatch
    ):
        """Unhappy/empty path: a window with no data → clean zeros, not error."""
        from mcp_bildsprache.server import generation_stats

        self._point_ledger_at(tmp_path, monkeypatch)
        stats = await generation_stats(days=7)
        assert "error" not in stats
        assert stats["totals"]["attempts"] == 0
        assert stats["totals"]["success_pct"] == 0.0
        assert stats["by_model"] == []

    @pytest.mark.anyio
    async def test_generation_stats_invalid_since_returns_error(
        self, tmp_path: Path, monkeypatch
    ):
        from mcp_bildsprache.server import generation_stats

        self._point_ledger_at(tmp_path, monkeypatch)
        stats = await generation_stats(since="not-a-date")
        assert stats["error"]["code"] == "INVALID_SINCE"


# ---------------------------------------------------------------------------
# generate_diagram (May 2026 brand-collapse follow-up)
# ---------------------------------------------------------------------------


class TestGenerateDiagramTool:
    @pytest.mark.anyio
    async def test_freetext_flow_routes_to_gemini(
        self, tmp_path: Path, mock_provider
    ):
        from mcp_bildsprache.server import generate_diagram

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_diagram(
                format="flow",
                prompt=(
                    "User submits form -> validation -> API call -> "
                    "response (success/error branches)"
                ),
            )

        # No error key — successful response.
        assert "error" not in result
        assert result["format"] == "flow"
        assert result["register"] == "professional"
        assert result["brand_context"] == "casey"
        assert "hosted_url" in result
        assert result["hosted_url"].startswith("https://img.cdit-works.de/casey/")
        # Model lineup refresh (CDI-1264): the diagram default prefers Nano
        # Banana Pro (gemini-3-pro-image-preview), passed as the model kwarg.
        assert mock_provider.await_args.kwargs.get("model") == "gemini-3-pro-image-preview"

    @pytest.mark.anyio
    async def test_mermaid_flow_input(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_diagram

        mermaid = """
        flowchart TD
            Start[User] --> Decision{Valid?}
            Decision -->|yes| Done[Success]
            Decision -->|no| Retry[Try again]
        """

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_diagram(format="flow", mermaid=mermaid)

        assert "error" not in result
        assert "hosted_url" in result

        # Verify the engineered prompt sent to the provider includes the
        # parsed structure (palette + node names).
        sent_prompt = mock_provider.await_args.args[0]
        assert "User" in sent_prompt
        assert "Success" in sent_prompt
        assert "#F4EFE3" in sent_prompt  # palette token

    @pytest.mark.anyio
    async def test_mermaid_sequence_input(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_diagram

        mermaid = """
        sequenceDiagram
            participant Browser
            participant API
            Browser->>API: GET /search
            API-->>Browser: 200 OK
        """

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_diagram(format="sequence", mermaid=mermaid)

        assert "error" not in result
        # Sequence default dimensions: portrait orientation.
        assert result["dimensions"] == "1200x1600"

    @pytest.mark.anyio
    async def test_mermaid_state_input(self, tmp_path: Path, mock_provider):
        from mcp_bildsprache.server import generate_diagram

        mermaid = """
        stateDiagram-v2
            [*] --> Idle
            Idle --> Active : start
            Active --> Idle : pause
            Active --> [*] : finish
        """

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_diagram(format="state", mermaid=mermaid)

        assert "error" not in result

    @pytest.mark.anyio
    async def test_openai_hint_routes_to_openai(
        self, tmp_path: Path, mock_provider
    ):
        from mcp_bildsprache.server import generate_diagram

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_diagram(
                format="flow",
                prompt="A simple flow",
                model_hint="openai",
            )

        assert "error" not in result
        # mock_provider returns model='gpt-image-2' by default.
        assert result["model"] == "gpt-image-2"

    @pytest.mark.anyio
    async def test_flux_hint_rejected(self):
        from mcp_bildsprache.server import generate_diagram

        result = await generate_diagram(
            format="flow",
            prompt="A flow",
            model_hint="flux",
        )

        assert "error" in result
        assert result["error"]["code"] == "PROVIDER_TEMPORARILY_DISABLED"
        assert result["error"]["provider"] == "FLUX"
        assert result["error"]["replacement"] == "gemini"  # diagram path replacement

    @pytest.mark.anyio
    async def test_no_input_rejected(self):
        from mcp_bildsprache.server import generate_diagram

        result = await generate_diagram(format="flow")
        assert "error" in result
        assert result["error"]["code"] == "INVALID_INPUT"

    @pytest.mark.anyio
    async def test_both_inputs_rejected(self):
        from mcp_bildsprache.server import generate_diagram

        result = await generate_diagram(
            format="flow", prompt="text", mermaid="flowchart TD\n  A --> B"
        )
        assert "error" in result
        assert result["error"]["code"] == "INVALID_INPUT"

    @pytest.mark.anyio
    async def test_unsupported_mermaid_type_rejected(self):
        from mcp_bildsprache.server import generate_diagram

        result = await generate_diagram(
            format="flow",
            mermaid="erDiagram\n  CUSTOMER ||--o{ ORDER : places",
        )
        assert "error" in result
        assert result["error"]["code"] == "MERMAID_PARSE_ERROR"
        assert "ER diagrams" in result["error"]["message"]

    @pytest.mark.anyio
    async def test_format_mismatch_rejected(self):
        from mcp_bildsprache.server import generate_diagram

        # Mermaid says sequenceDiagram, format says flow.
        result = await generate_diagram(
            format="flow",
            mermaid="sequenceDiagram\n  A->>B: hello",
        )
        assert "error" in result
        assert result["error"]["code"] == "MERMAID_FORMAT_MISMATCH"

    @pytest.mark.anyio
    async def test_diagram_personal_register_default_overridable(
        self, tmp_path: Path, mock_provider
    ):
        from mcp_bildsprache.server import generate_diagram

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_diagram(
                format="flow",
                prompt="A flow",
                register="personal",
            )

        assert result["register"] == "personal"
        sent_prompt = mock_provider.await_args.args[0]
        # Personal register tilts toward warmer language.
        assert "warmer" in sent_prompt.lower() or "kitchen-table" in sent_prompt.lower()

    @pytest.mark.anyio
    async def test_attribution_payload_present(
        self, tmp_path: Path, mock_provider
    ):
        from mcp_bildsprache.server import generate_diagram

        with patch("mcp_bildsprache.server.settings") as s, \
             patch("mcp_bildsprache.storage.settings") as ss:
            s.enable_hosting = True
            ss.image_storage_path = str(tmp_path)
            ss.image_domain = "https://img.cdit-works.de"

            result = await generate_diagram(format="flow", prompt="test diagram")

        assert "ai_attribution" in result
        attr = result["ai_attribution"]
        assert attr["schema_version"]
        assert attr["provider"]
        assert attr["model"]
