# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FastMCP server that exposes brand-aware image generation as MCP tools. The active dispatched providers are OpenAI (gpt-image-2 raster default + gpt-image-1.5 quality sibling) and Google Gemini (Nano Banana Pro `gemini-3-pro-image-preview` for diagrams + Nano Banana 2 `gemini-3.1-flash-image-preview` raster fallback). Black Forest Labs FLUX and Recraft V4.1 remain in-tree but disabled at the dispatcher. It injects a brand visual preset, generates via the provider API, then runs a post-processing pipeline (resize/crop → WebP → EXIF provenance) and stores the result on disk to be served under `https://img.cdit-works.de`.

### MCP tool surface

`server.py` exposes five tools: `generate_image` (the full raster pipeline described below), `generate_diagram` (Mermaid-aware flow/sequence/state diagrams via Gemini Nano Banana Pro by default), `generate_prompt` (prompt engineering only, no provider call), `list_models` (capabilities/costs per provider — splits active vs. disabled), and `get_visual_presets` (returns the `PRESETS` dict + `CASEY_REGISTER_OVERLAYS`, optionally filtered by `context` and `register`). When adding tools, keep the heavy lifting in helper modules — tool bodies should stay thin orchestrators.

### Module map

- `server.py` — FastMCP app, tool definitions, orchestration, HTTP static mount.
- `providers/` — one module per provider. Each exports an async `generate_*(prompt, width, height, ...)` returning `ProviderResult`. Dumb bytes-fetchers; no brand/sizing logic. Per the May 2026 brand-collapse change, `providers/bfl.py` and `providers/recraft.py` remain in-tree but are not reachable via the dispatcher (`route_model` raises `ProviderTemporarilyDisabled` on FLUX/Recraft hints).
- `presets.py` — `PRESETS` (active brands: `casey`, `yorizon`), `CASEY_PALETTE`, `CASEY_REGISTER_OVERLAYS`, `PLATFORM_SIZES`, `route_model` (intent="raster"|"diagram"), `get_dimensions`, `get_preset(context, register)`. `ACTIVE_PROVIDERS` and `DISABLED_PROVIDERS` are surfaced via `list_models`.
- `diagrams.py` — `parse_mermaid` (flowchart/sequenceDiagram/stateDiagram only) and `compose_render_brief` (palette-injected, register-tilted prompt for the image model). Other Mermaid types raise `MermaidParseError`.
- `pipeline.py` — `process_image` (resize/crop → WebP → EXIF).
- `storage.py` — `store_image` / `store_raw_image`, slug collisions, JSON sidecars.
- `slugs.py` — slug generation + `BRAND_PREFIXES` (URL-path dir per brand). New `casey/` prefix; legacy `casey-berlin/` and `cdit/` paths preserved on the static mount for historical URLs.
- `config.py` — pydantic-settings env surface (API keys, `TRANSPORT`, Keycloak/API-key auth vars, data dir).
- `types.py` — shared dataclasses, notably `ProviderResult(image_data, mime_type, model, cost_estimate, usage?, revised_prompt?, model_version?)` and `ProviderTemporarilyDisabled` exception.
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
- **FLUX has its own internal fallback chain** (`flux-2-max → flux-2-pro → flux-pro-1.1`) inside `providers/bfl.py`, separate from the cross-provider `FALLBACKS` map in `server.py`. These compose: BFL retries within FLUX first, then server-level fallback hops to Gemini. **When `reference_images` are present the chain switches to `flux-2-pro (image_prompt)` and `flux-2-max` is never attempted** — falling to a text-only model would silently lose the identity signal. (`flux-kontext-pro` was dropped in the model lineup refresh, CDI-1264; FLUX/Recraft remain disabled at the dispatcher regardless.)
- **FLUX dimension snapping**: each FLUX model has `snap` (grid) and `max_mp` constraints. The provider snaps before submission; the final pipeline re-crops to the caller's exact target dimensions. This means provider output dimensions often differ from the final output.
- **Routing**: `route_model` defaults to FLUX for everything except vector-flavored platforms (icon/svg/logo/illustration keywords → Recraft). Gemini is never auto-selected — only via explicit `model_hint` or as a fallback. **When `has_references=True` the vector-platform override to Recraft is skipped** (Recraft would drop the refs); explicit `model_hint="recraft"` is still honoured.

### Brand presets

`presets.py::PRESETS` is the source of truth for visual DNA per brand context. Active brands (May 2026 brand collapse): `casey` (one voice, two registers — `personal` and `professional`) and `yorizon` (fully isolated, no shared palette tokens). Legacy keys (`casey-berlin`, `cdit-works`, `casey.berlin`, `@cdit`, `storykeep`, `nah`, ...) all normalise to `casey` via `mcp_bildsprache.brands.normalize_brand`.

The `casey` preset injects the locked botanical palette from the 7 May 2026 brand-decisions doc: paper bone `#F4EFE3` (background, ~70% of surface), forest moss `#2C4A38` (primary form), pine ink `#1F2E26` (body text), weathered ochre `#B8884A` (accent ≤5%), soft moss `#C7CFB8` (hairlines). Vollkorn-style typography and anti-anchor exclusions (chrome, lens flare, neon, gradient mesh, generic AI aesthetic) are part of the base preset. Per-register overlays (`CASEY_REGISTER_OVERLAYS`) tilt prompt direction: personal = warmer / kitchen-table / lower contrast; professional = crisper / schematic / higher contrast.

`slugs.py::BRAND_PREFIXES` maps brand keys to URL-path directories. New generations land under `casey/`. Legacy `casey-berlin/` and `cdit/` directories stay populated and continue to serve historical URLs (no backfill).

`PLATFORM_SIZES` is the auto-sizing table. Adding a platform requires updating the `Platform` `Literal` in `server.py` too.

### Provider routing (May 2026 collapse)

`presets.py::route_model(intent="raster"|"diagram", model_hint?, ...)`:

- `intent="raster"` (default for `generate_image`): default → OpenAI gpt-image-2. Gemini Nano Banana is the cross-provider fallback.
- `intent="diagram"` (used by `generate_diagram`): default → Gemini Nano Banana Pro. OpenAI gpt-image-2 available via `model_hint="openai"`.
- `model_hint="flux"` / `"flux-*"` / `"recraft"` → raises `ProviderTemporarilyDisabled`. The replacement message names the active provider for the caller's intent (openai for raster, gemini for diagram).
- `providers/bfl.py` and `providers/recraft.py` remain importable + tested for shape conformance, so re-enabling is a one-PR dispatcher swap. `BFL_API_KEY` and `RECRAFT_API_KEY` env vars are still recognised but unused.

Tier 1 OpenAI rate-limit posture: existing `_post_with_backoff` (1s/4s/10s + jitter) absorbs 429s. Sequential dispatch — no parallel fan-out in v1. `event=image_generated` and `event=diagram_generated` log lines support cost aggregation via Komodo log queries.

### Diagram tool (`generate_diagram`)

`diagrams.py::parse_mermaid` covers `flowchart`/`graph`, `sequenceDiagram`, `stateDiagram`/`stateDiagram-v2`. Other graph types (`classDiagram`, `erDiagram`, `gantt`, `pie`, `gitGraph`, `mindmap`, `timeline`, `journey`, `quadrantChart`, `requirementDiagram`) raise `MermaidParseError` with a hint pointing at the supported set.

`compose_render_brief(parsed, prompt, format, register)`: builds the engineered prompt sent to the image model. Always injects the botanical palette + Vollkorn typography + anti-caps rule. Format-specific UML conventions (lifelines/horizontal arrows/activation boxes for sequence; rounded boxes/filled circle/double-circle for state) are baked into the brief regardless of input shape (Mermaid or free-text).

`generate_diagram` writes output to `/data/images/casey/` and the gallery indexes it like any other image. Default dimensions: `1600x900` for flow/state, `1200x1600` for sequence (taller for readability).

### Identity packs

Brand presets handle *visual DNA* (palette, mood, composition). Identity packs handle *personal likeness* for the casey brand (Casey + his two Stabyhoun dogs, Fimme and Sien).

Identity packs live on the `identity-data` Docker volume, mounted **read-only** at `/data/identity/<brand-dir>/`. Each brand has its own `manifest.json` plus reference images. Nothing identity-related is committed to this repo — see `docs/identity/README.md` for the volume contract and `docs/identity/manifest.example.json` for the schema.

- **Loader**: `mcp_bildsprache/identity.py::load_identity_packs` runs at server startup, caches packs in a module-level dict. Missing/malformed manifests → WARN once, server keeps running with text-only prompts.
- **Resolver**: `resolve_identity_for_call(pack, prompt, include_dogs)` returns a deterministic list of reference-image paths (manifest declaration order). Person-excluding markers (`"icon"`, `"flat illustration"`, `"abstract pattern"`, `"logo"`, `"architectural detail"`, `"svg"`) short-circuit to `[]`.
- **Composition clause**: `presets.py::CASEY_COMPOSITION_CLAUSE` is prepended to the enhanced prompt *only* when the identity pack resolves to a non-empty list and the resolved canonical brand is `casey` (covers all legacy aliases). The gating lives in `server.py` so person-excluding prompts stay clean.
- **`list_models`** returns `identity_packs: {brand: bool}`; `get_visual_presets(context=...)` returns `identity_pack_loaded: bool`.
- **Volume rename in flight**: production may be on `/data/identity/casey-berlin/` (pre-rename) or `/data/identity/casey/` (post-rename). The loader handles both and `get_pack_for_context` tries multiple candidate keys (`casey`, `@casey`, `casey-berlin`, `@casey-berlin`, `@casey.berlin`) so deploy ordering can't break the lookup.
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

Auth is hostname-based: `gallery/middleware.py::TailnetOnlyMiddleware` rejects `/gallery/*` requests whose `Host` header doesn't match `GALLERY_TAILNET_HOSTNAME` with HTTP 404 (not 403 — don't advertise existence). Production hostname: `bildsprache.onca-blenny.ts.net` (set via the docktail `service.name=bildsprache` label in `compose.yaml` plus `GALLERY_TAILNET_HOSTNAME` env). When the env var is unset, the middleware is a no-op and logs one startup WARN. Other paths (`/mcp`, `/<brand>/*.webp`) are never gated. The container is exposed on the Tailnet by docktail (Tailscale serve via labels) — no separate `tailscale serve` config required.

Bulk download is client-side: the frontend `fetch`es selected WebPs, feeds them to the vendored `fflate` (`gallery/static/fflate.min.js`, version pinned — see the neighboring `README.md` for SHA-256), and triggers a single Blob URL download. This is what makes it work on iOS Safari.

### Auth (HTTP mode only)

`auth.py::create_auth` returns a `MultiAuth` composed of:
- **OIDCProxy** for Keycloak (realm `cdit-mcp`, audience `mcp-bildsprache`) — this is the path Claude.ai connectors take. No DCR; credentials are pre-registered.
- **BearerTokenVerifier** for a static API key prefixed `bmcp_` — used by Claude Code, n8n, scripts.

Auth in HTTP mode is **fail-fast** (see commit `c637e42`): `_build_auth()` reads `MCP_API_KEY` (fleet standard) with fallback to `MCP_BILDSPRACHE_API_KEY` and raises `SystemExit` if neither is set, rather than silently running unauthenticated. If `KEYCLOAK_CLIENT_SECRET` is set, the server returns the full `MultiAuth` (Keycloak + bearer); if only the API key is set, the server returns a `BearerTokenVerifier` alone (the current production shape post-Keycloak-decommission).

Stdio mode skips auth entirely.

## Production deployment

The server runs on the `nebula-1` host as a Docker compose stack `git-mcp-bildsprache-nebula` (container `git-mcp-bildsprache-nebula-mcp-bildsprache-1`), port `8007` → container `8000`. Static images hosted at `https://img.cdit-works.de`; MCP endpoint at `https://mcp-bildsprache.cdit-dev.de/mcp`. `FASTMCP_HOME=/data/fastmcp` and two named volumes (`fastmcp-data`, `images-data`) persist state. The stack uses `build: .` rather than pulling from ghcr — the release workflow still publishes images to `ghcr.io/caseyro/mcp-bildsprache` but production builds locally on each `deploy-stack`. This means the `/health` version string reflects whatever was in `pyproject.toml` at deploy time, which can lag the most recent CI release commit by one bump.

## Single source of truth

There is no longer a local `~/.claude/skills/bildsprache/` skill or an `install.sh` distribution path — this MCP server is the only way to reach Bildsprache. Brand visual presets live in `mcp_bildsprache/presets.py`; identity packs live on the `identity-data` volume; the AI-attribution contract is mirrored from `CaseyRo/CDiT-marketingskills/shared/` via `.github/workflows/shared-contract-check.yml`. When you change brand DNA, model routing, or sizing, edit it here and let CI ship — every client (Claude.ai, Claude Code, n8n) gets the change from the same server.
