# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FastMCP server that exposes brand-aware image generation as MCP tools. It routes prompts to one of three providers (Google Gemini, Black Forest Labs FLUX, Recraft V4), injects a brand visual preset, generates via the provider API, then runs a post-processing pipeline (resize/crop → WebP → EXIF provenance) and stores the result on disk to be served under `https://img.cdit-works.de`.

### MCP tool surface

`server.py` exposes four tools: `generate_image` (the full pipeline described below), `generate_prompt` (prompt engineering only, no provider call), `list_models` (capabilities/costs per provider), and `get_visual_presets` (returns the `PRESETS` dict, optionally filtered by `context`). When adding tools, keep the heavy lifting in helper modules — tool bodies should stay thin orchestrators.

### Module map

- `server.py` — FastMCP app, tool definitions, orchestration, HTTP static mount.
- `providers/` — one module per provider. Each exports an async `generate_*(prompt, width, height, ...)` returning `ProviderResult`. Dumb bytes-fetchers; no brand/sizing logic.
- `presets.py` — `PRESETS`, `PLATFORM_SIZES`, `route_model`, `get_dimensions`, `get_preset`.
- `pipeline.py` — `process_image` (resize/crop → WebP → EXIF).
- `storage.py` — `store_image` / `store_raw_image`, slug collisions, JSON sidecars.
- `slugs.py` — slug generation + `BRAND_PREFIXES` (URL-path dir per brand).
- `config.py` — pydantic-settings env surface (API keys, `TRANSPORT`, Keycloak/API-key auth vars, data dir).
- `types.py` — shared dataclasses, notably `ProviderResult(image_data, mime_type, model, cost_estimate)`.
- `auth.py` — `create_auth` returning the composed `MultiAuth` for HTTP mode.

Package: `mcp_bildsprache` · Entry point: `mcp-bildsprache = mcp_bildsprache.server:main` · Python ≥3.11.

## Commands

```bash
# Install (editable) with dev deps
uv sync

# Run locally in HTTP mode (what production uses)
GEMINI_API_KEY=... BFL_API_KEY=... RECRAFT_API_KEY=... TRANSPORT=http uv run mcp-bildsprache

# Stdio mode (default — for local MCP clients like Claude Desktop)
uv run mcp-bildsprache

# Tests
uv run pytest                           # full suite
uv run pytest tests/test_pipeline.py    # single file
uv run pytest tests/test_storage.py::TestStoreImage::test_stores_and_returns_url  # single test
uv run pytest -x                        # stop on first failure (what CI runs)

# Lint
uv run ruff check .

# Docker (mirrors production)
docker compose up --build
```

`asyncio_mode = "auto"` is set in `pyproject.toml` — do not add `@pytest.mark.asyncio` decorators.

Tests mirror modules one-to-one: `tests/test_<module>.py` holds unit tests for `mcp_bildsprache/<module>.py` (plus `test_integration.py` for end-to-end flows). When adding a module, add the matching test file — don't scatter new tests into `test_integration.py`.

## Release flow

`main` is the release branch. The `release.yml` workflow runs on every push to `main` (skips if commit contains `[skip ci]` or only `*.md`/`tests/**` changed):

1. `uv sync` + `uv run pytest -x`
2. Bumps patch version in `pyproject.toml`, prepends a CHANGELOG entry from `git log`, commits as `chore(release): v<new> [skip ci]`, tags `v<new>`, pushes.
3. Builds multi-arch (amd64/arm64) image, pushes to `ghcr.io/<repo>:<version>` and `:latest`.

Implication: **do not hand-bump the version** in `pyproject.toml` — CI owns it. Do not add a CHANGELOG entry manually; CI generates one from commit messages.

## Architecture

### Request flow (generate_image)

`server.py` orchestrates. Provider modules only fetch bytes; everything else (brand injection, sizing, post-processing, storage) lives in the package.

```
tool call
  → get_pack_for_context(context)               [identity.py]   # loaded at startup
  → resolve_identity_for_call(pack, prompt,
      include_dogs)                             [identity.py]   # [] if person-excluding
  → read reference bytes (cached per process)   [server.py]
  → route_model(context, platform, model_hint,
      has_references=bool(refs))                [presets.py]    # picks "flux"|"gemini"|"recraft"
  → get_dimensions(platform) or explicit WxH    [presets.py]
  → get_preset(context) + [composition clause if @casey.berlin + refs]
    + prompt + mood                             [presets.py]    # enhanced_prompt string
  → PROVIDERS[key](enhanced_prompt, w, h,
      reference_images=refs)                    [providers/*]   # returns ProviderResult(bytes, mime, model, cost)
      └── on Exception → (REFERENCE_FALLBACKS if refs else FALLBACKS)[key] provider
  → process_image(...)                          [pipeline.py]   # resize+crop (ImageOps.fit) → WebP → EXIF
  → store_image(...)                            [storage.py]    # writes /data/images/<brand>/<slug>.webp + .json sidecar
  → (optional) store_raw_image(...)             [storage.py]    # provider-original bytes, "-raw" suffix
  → returns {hosted_url, model, cost_estimate, fallback_used?, ...}
```

Key invariants:
- **Provider layer is dumb**: it submits a prompt (plus optional `reference_images`) and returns raw bytes + metadata. All brand/sizing/identity logic is upstream; all processing is downstream. Do not bake brand presets into providers.
- **FLUX has its own internal fallback chain** (`flux-2-max → flux-2-pro → flux-pro-1.1`) inside `providers/bfl.py`, separate from the cross-provider `FALLBACKS` map in `server.py`. These compose: BFL retries within FLUX first, then server-level fallback hops to Gemini. **When `reference_images` are present the chain switches to `flux-kontext-pro → flux-2-pro (image_prompt)` and `flux-2-max` is never attempted** — falling to a text-only model would silently lose the identity signal.
- **FLUX dimension snapping**: each FLUX model has `snap` (grid) and `max_mp` constraints. The provider snaps before submission; the final pipeline re-crops to the caller's exact target dimensions. This means provider output dimensions often differ from the final output.
- **Routing**: `route_model` defaults to FLUX for everything except vector-flavored platforms (icon/svg/logo/illustration keywords → Recraft). Gemini is never auto-selected — only via explicit `model_hint` or as a fallback. **When `has_references=True` the vector-platform override to Recraft is skipped** (Recraft would drop the refs); explicit `model_hint="recraft"` is still honoured.

### Brand presets

`presets.py::PRESETS` is the source of truth for visual DNA per brand context (`@casey.berlin`, `@cdit`, `@storykeep`, `@nah`, `@yorizon`). These strings are prepended to every prompt for that context. `get_preset` falls back to `cdit-works.de` for unknown contexts.

`slugs.py::BRAND_PREFIXES` maps the same contexts to URL-path directories (e.g. `casey.berlin → casey-berlin/`). Unknown context → `gen/`. Keep these two maps in sync when adding a brand.

`PLATFORM_SIZES` is the auto-sizing table. Adding a platform requires updating the `Platform` `Literal` in `server.py` too (it's duplicated; the `Literal` constrains MCP tool schemas).

### Identity packs

Brand presets handle *visual DNA* (palette, mood, composition). Identity packs handle *personal likeness* for brands where a specific person/subject appears on camera (`@casey.berlin`: Casey + his two Stabyhoun dogs, Fimme and Sien).

Identity packs live on the `identity-data` Docker volume, mounted **read-only** at `/data/identity/<brand-dir>/`. Each brand has its own `manifest.json` plus reference images. Nothing identity-related is committed to this repo — see `docs/identity/README.md` for the volume contract and `docs/identity/manifest.example.json` for the schema.

- **Loader**: `mcp_bildsprache/identity.py::load_identity_packs` runs at server startup, caches packs in a module-level dict. Missing/malformed manifests → WARN once, server keeps running with text-only prompts.
- **Resolver**: `resolve_identity_for_call(pack, prompt, include_dogs)` returns a deterministic list of reference-image paths (manifest declaration order). Person-excluding markers (`"icon"`, `"flat illustration"`, `"abstract pattern"`, `"logo"`, `"architectural detail"`, `"svg"`) short-circuit to `[]`.
- **Composition clause**: `presets.py::CASEY_COMPOSITION_CLAUSE` is prepended to the enhanced prompt *only* when the identity pack resolves to a non-empty list for `@casey.berlin`. The gating lives in `server.py` so person-excluding prompts stay clean.
- **`list_models`** returns `identity_packs: {brand: bool}`; `get_visual_presets(context=...)` returns `identity_pack_loaded: bool`.
- **Static mount hygiene**: `_mount_static_files` mounts `image_storage_path` only — `/data/identity` is never exposed via `img.cdit-works.de`. A regression test enforces this.

### Storage layout

```
/data/images/
  <brand-prefix>/
    <slug>-<WxH>.webp           # processed WebP (what hosted_url points to)
    <slug>-<WxH>.json           # sidecar: prompt, prompt_hash, model, cost, dims, file_size
    <slug>-<WxH>-raw.<ext>      # optional raw provider output (when raw=true)
    <slug>-<WxH>-<4hex>.webp    # collision suffix (sha256 of bytes, first 4 hex)
```

Slug collisions (same prompt+dimensions+brand) get a 4-hex suffix derived from image bytes. The JSON sidecar never stores the raw prompt in EXIF — only a SHA-256 hash is embedded in `UserComment` (for provenance without leaking prompt content in the file itself). The sidecar file does store the full prompt.

### HTTP serving

In HTTP mode, `server.py::main()` calls `mcp.http_app(transport="http")` (FastMCP 3.2.x API) and then `_mount_static_files(app)` mounts `/data/images` at `/`. This is what makes hosted URLs like `https://img.cdit-works.de/cdit/foo-1200x630.webp` resolve. The `/mcp` path is reserved for the MCP protocol. `mimetypes.add_type("image/webp"/".avif")` is needed because `python:3.12-slim` does not register them by default (see commit `406df0c`).

#### Gallery (Tailnet-only)

`server.py::_mount_gallery(app)` inserts a Starlette sub-app at `/gallery` **before** the root static mount, so the prefix wins routing. The sub-app's routes are:

- `GET  /gallery/`                  → vanilla JS shell (`gallery/static/index.html`)
- `GET  /gallery/static/<path>`     → CSS/JS/`fflate.min.js`
- `GET  /gallery/api/images`        → filtered + paged list (query: `brand`, `platform`, `from`, `to`, `q`, `min_width`, `min_height`, `sort`, `limit ≤ 500`, `offset`)
- `GET  /gallery/api/images/<path>` → single entry (deep links)
- `POST /gallery/api/reindex`       → synchronous rescan of `/data/images/**/*.json`

The index lives in memory (`gallery/index.py::GalleryIndex`), built by walking JSON sidecars. It's rebuilt on Starlette startup (blocking), on a background timer (`GALLERY_REINDEX_INTERVAL_SECONDS`, default 300), and on demand via the reindex endpoint. There is no database and no file watcher.

Auth is hostname-based: `gallery/middleware.py::TailnetOnlyMiddleware` rejects `/gallery/*` requests whose `Host` header doesn't match `GALLERY_TAILNET_HOSTNAME` with HTTP 404 (not 403 — don't advertise existence). When the env var is unset, the middleware is a no-op and logs one startup WARN. Other paths (`/mcp`, `/<brand>/*.webp`) are never gated.

Bulk download is client-side: the frontend `fetch`es selected WebPs, feeds them to the vendored `fflate` (`gallery/static/fflate.min.js`, version pinned — see the neighboring `README.md` for SHA-256), and triggers a single Blob URL download. This is what makes it work on iOS Safari.

### Auth (HTTP mode only)

`auth.py::create_auth` returns a `MultiAuth` composed of:
- **OIDCProxy** for Keycloak (realm `cdit-mcp`, audience `mcp-bildsprache`) — this is the path Claude.ai connectors take. No DCR; credentials are pre-registered.
- **BearerTokenVerifier** for a static API key prefixed `bmcp_` — used by Claude Code, n8n, scripts.

Auth in HTTP mode is **fail-fast** (see commit `c637e42`): `_build_auth()` reads `MCP_API_KEY` (fleet standard) with fallback to `MCP_BILDSPRACHE_API_KEY` and raises `SystemExit` if neither is set, rather than silently running unauthenticated. If `KEYCLOAK_CLIENT_SECRET` is set, the server returns the full `MultiAuth` (Keycloak + bearer); if only the API key is set, the server returns a `BearerTokenVerifier` alone (the current production shape post-Keycloak-decommission).

Stdio mode skips auth entirely.

## Production deployment

The server runs on the `ubuntu-smurf-mirror` host as a Docker compose stack, port `8007` → container `8000`, image hosted at `https://img.cdit-works.de`, MCP endpoint at `https://bildsprache.cdit-dev.de/mcp`. `FASTMCP_HOME=/data/fastmcp` and two named volumes (`fastmcp-data`, `images-data`) persist state.
