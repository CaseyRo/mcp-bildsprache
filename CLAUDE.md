# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FastMCP 4 server that exposes brand-aware image generation as MCP tools. The active dispatched providers are OpenAI (raster default at medium quality: gpt-image-2 for identity scenes, gpt-image-2.5-flare otherwise, via `server.py::_pick_raster_model`; `OPENAI_IMAGE_MODEL` forces one; `model` can pick `gpt-image-2.5-flare` / `gpt-image-2.5-sunburst` per call, with `quality` and `transparent` options) and Google Gemini (Nano Banana Pro `gemini-3-pro-image-preview` for diagrams + Nano Banana 2 `gemini-3.1-flash-image-preview`, raster by explicit hint only). There is no automatic cross-provider fallback: a provider error fails the job. The FLUX and Recraft modules and the `FALLBACKS` map were deleted on 2026-06-30; FLUX/Recraft hints are still rejected with `ProviderTemporarilyDisabled` so the tool schema did not change. It injects a brand visual preset, generates via the provider API, then runs a post-processing pipeline (resize/crop → WebP → EXIF provenance) and stores the result on disk to be served under `https://img.cdit-works.de`.

### MCP tool surface

`server.py` exposes these tools: `generate_image` (the full raster pipeline described below), `generate_diagram` (Mermaid-aware flow/sequence/state diagrams via Gemini Nano Banana Pro by default), `get_image_result` (CDI-1266 — poll for an async-dispatched render's result by `job_id`), `generate_prompt` (prompt engineering only, no provider call), `list_models` (capabilities/costs per provider — splits active vs. disabled), `get_visual_presets` (returns the `PRESETS` dict + `CASEY_REGISTER_OVERLAYS`, optionally filtered by `context` and `register`), `list_recent_generations` (newest-first artifact index — recovery path), and `generation_stats` (per-model outcome stats from the CDI-1264 ledger). When adding tools, keep the heavy lifting in helper modules — tool bodies should stay thin orchestrators.

**Async dispatch+poll (CDI-1266):** `generate_image` / `generate_diagram` reach clients through a Cloudflare-managed MCP portal with a hard ~60s upstream read timeout. gpt-image-2 / Nano-Banana-Pro renders take 50-80s, so a synchronous response is severed (`-32001`) even though the render completes server-side. Fix: the render is dispatched on a DETACHED background task (`jobs.spawn_detached` — a module-level strong-ref task set on the running loop, NOT bound to the request's cancellation scope, so it survives request teardown) and the tool inline-waits only up to `SYNC_WAIT_SECONDS` (default 20s, under the portal limit). Fast renders return the `hosted_url` inline (backward compatible); slow ones return `{job_id, status: "pending", poll_with: "get_image_result"}`. `job_id` IS the CDI-1264 ledger `request_id`, so `get_image_result` resolves from the in-process registry first and falls back to the durable ledger by that id (recoverable across restarts/workers). The detached render owns its own ledger write (success AND failure) — the CDI-1264 ledger still fires on the async path.

### Module map

- `server.py` — FastMCP app, tool definitions, orchestration, HTTP static mount.
- `providers/` — `openai.py` and `gemini.py`. Each exports an async `generate_*(prompt, width, height, ...)` returning `ProviderResult`. Dumb bytes-fetchers; no brand/sizing logic.
- `presets.py` — `PRESETS` (active brands: `casey`, `yorizon`), `CASEY_PALETTE`, `CASEY_REGISTER_OVERLAYS`, `PLATFORM_SIZES`, `route_model` (intent="raster"|"diagram"), `get_dimensions`, `get_preset(context, register)`. `ACTIVE_PROVIDERS` and `DISABLED_PROVIDERS` are surfaced via `list_models`.
- `diagrams.py` — `parse_mermaid` (flowchart/sequenceDiagram/stateDiagram only) and `compose_render_brief` (palette-injected, register-tilted prompt for the image model). Other Mermaid types raise `MermaidParseError`.
- `pipeline.py` — `process_image` (resize/crop → WebP → EXIF).
- `jobs.py` — async dispatch+poll core (CDI-1266): `JobRegistry` (in-process `job_id -> JobRecord`, pending|done|error + long-poll `wait_for`) and `spawn_detached` (run a render on the event loop detached from the request scope, held by a module-level strong-ref set so it survives request teardown and isn't GC'd mid-flight).
- `ledger.py` — append-only JSONL outcome ledger (CDI-1264) + `find_by_request_id` (the CDI-1266 durable fallback used by `get_image_result`).
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
OPENAI_API_KEY=... GEMINI_API_KEY=... MCP_BILDSPRACHE_API_KEY=... TRANSPORT=http uv run mcp-bildsprache

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

`main` is protected and is the release branch. The `release.yml` workflow runs on every push to `main` (skips if the commit contains `[skip ci]` or only `*.md`/`tests/**` changed):

1. `uv sync` + `uv run pytest -x`, then `pip-audit`.
2. Tags the next patch `v<new>` (tag-only: nothing is committed back, `pyproject.toml` and the CHANGELOG stay static).
3. Builds a multi-arch image to `ghcr.io/<repo>:<version>` and `:latest`. Production does not use it (see Production deployment).

`pyproject.toml`'s `version` therefore lags the tags; `/health` reports it plus the `git_commit` baked at build time.

## Architecture

### Request flow (generate_image)

`server.py` orchestrates. Provider modules only fetch bytes; everything else (brand injection, sizing, post-processing, storage) lives in the package.

```
tool call
  → get_pack_for_context(context)               [identity.py]   # loaded at startup
  → resolve_identity_for_call(pack, prompt,
      include_dogs, include_people)             [identity.py]   # [] if person-excluding
  → read reference bytes (cached per process)   [server.py]
  → route_model(context, platform, model_hint,
      has_references=bool(refs))                [presets.py]    # picks "openai"|"gemini"
  → get_dimensions(platform) or explicit WxH    [presets.py]
  → get_preset(context) + [composition clause if @casey.berlin + refs]
    + prompt + mood                             [presets.py]    # enhanced_prompt string
  → PROVIDERS[key](enhanced_prompt, w, h,
      reference_images=refs)                    [providers/*]   # returns ProviderResult(bytes, mime, model, cost)
      └── on Exception → ledger row + job error (no fallback provider)
  → process_image(...)                          [pipeline.py]   # resize+crop (ImageOps.fit) → WebP → EXIF
  → store_image(...)                            [storage.py]    # writes /data/images/<brand>/<slug>.webp + .json sidecar
  → (optional) store_raw_image(...)             [storage.py]    # provider-original bytes, "-raw" suffix
  → returns {hosted_url, model, cost_estimate, fallback_used?, ...}
```

Key invariants:
- **Provider layer is dumb**: it submits a prompt (plus optional `reference_images`) and returns raw bytes + metadata. All brand/sizing/identity logic is upstream; all processing is downstream. Do not bake brand presets into providers.
- **No fallback provider.** A provider error is recorded in the ledger and fails the job. With reference images present, never add a fallback to a text-only model: the identity signal would be lost silently.
- **Routing**: `route_model` sends raster to OpenAI and diagrams to Gemini; an explicit `model_hint` wins. See *Provider routing* below.

### Brand presets

`presets.py::PRESETS` is the source of truth for visual DNA per brand context. Active brands (May 2026 brand collapse): `casey` (one voice, two registers — `personal` and `professional`) and `yorizon` (fully isolated, no shared palette tokens). Legacy keys (`casey-berlin`, `cdit-works`, `casey.berlin`, `@cdit`, `storykeep`, `nah`, ...) all normalise to `casey` via `mcp_bildsprache.brands.normalize_brand`.

The `casey` preset injects the locked botanical palette from the 7 May 2026 brand-decisions doc: paper bone `#F4EFE3` (background, ~70% of surface), forest moss `#2C4A38` (primary form), pine ink `#1F2E26` (body text), weathered ochre `#B8884A` (accent ≤5%), soft moss `#C7CFB8` (hairlines). Vollkorn-style typography and anti-anchor exclusions (chrome, lens flare, neon, gradient mesh, generic AI aesthetic) are part of the base preset. The preset carries no brand name or tagline: `get_preset(..., prompt=)` adds the Vollkorn typography clause only when the prompt asks for text/logo/wordmark (`prompt_requests_text`), otherwise `NO_BRAND_TEXT_CLAUSE` (Track B5 — models were painting "casey" wordmarks and taglines into scenes). Per-register overlays (`CASEY_REGISTER_OVERLAYS`) tilt prompt direction: personal = warmer / kitchen-table / lower contrast; professional = crisper / schematic / higher contrast.

`slugs.py::BRAND_PREFIXES` maps brand keys to URL-path directories. New generations land under `casey/`. Legacy `casey-berlin/` and `cdit/` directories stay populated and continue to serve historical URLs (no backfill).

`PLATFORM_SIZES` is the auto-sizing table. Adding a platform requires updating the `Platform` `Literal` in `server.py` too.

### Provider routing (May 2026 collapse)

`presets.py::route_model(intent="raster"|"diagram", model_hint?, ...)`:

- `intent="raster"` (default for `generate_image`): default → OpenAI; `_pick_raster_model` chooses gpt-image-2 for identity scenes and gpt-image-2.5-flare otherwise. Gemini is reachable only by explicit hint.
- `intent="diagram"` (used by `generate_diagram`): default → Gemini Nano Banana Pro. OpenAI gpt-image-2 available via `model_hint="openai"`.
- `model_hint="flux"` / `"flux-*"` / `"recraft"` → raises `ProviderTemporarilyDisabled`. The replacement message names the active provider for the caller's intent (openai for raster, gemini for diagram).
- The FLUX/Recraft modules were deleted on 2026-06-30 (only the rejection remains). Re-adding a provider means a new `providers/` module plus a `PROVIDERS` entry in `server.py`.

Tier 1 OpenAI rate-limit posture: existing `_post_with_backoff` (1s/4s/10s + jitter) absorbs 429s. Sequential dispatch — no parallel fan-out in v1. `event=image_generated` and `event=diagram_generated` log lines support cost aggregation via container log queries.

### Diagram tool (`generate_diagram`)

`diagrams.py::parse_mermaid` covers `flowchart`/`graph`, `sequenceDiagram`, `stateDiagram`/`stateDiagram-v2`. Other graph types (`classDiagram`, `erDiagram`, `gantt`, `pie`, `gitGraph`, `mindmap`, `timeline`, `journey`, `quadrantChart`, `requirementDiagram`) raise `MermaidParseError` with a hint pointing at the supported set.

`compose_render_brief(parsed, prompt, format, register)`: builds the engineered prompt sent to the image model. Always injects the botanical palette + Vollkorn typography + anti-caps rule. Format-specific UML conventions (lifelines/horizontal arrows/activation boxes for sequence; rounded boxes/filled circle/double-circle for state) are baked into the brief regardless of input shape (Mermaid or free-text).

`generate_diagram` writes output to `/data/images/casey/` and the gallery indexes it like any other image. Default dimensions: `1600x900` for flow/state, `1200x1600` for sequence (taller for readability).

### Identity packs

Brand presets handle *visual DNA* (palette, mood, composition). Identity packs handle *personal likeness* for the casey brand (a person plus companion animals).

Identity packs live on the `identity-data` Docker volume, mounted **read-only** at `/data/identity/<brand-dir>/`. Each brand has its own `manifest.json` plus reference images. Nothing identity-related is committed to this repo — see `docs/identity/README.md` for the volume contract and `docs/identity/manifest.example.json` for the schema.

- **Loader**: `mcp_bildsprache/identity.py::load_identity_packs` runs at server startup, caches packs in a module-level dict. Missing/malformed manifests → WARN once, server keeps running with text-only prompts.
- **Resolver**: `resolve_identity_for_call(pack, prompt, include_dogs, include_people)` returns a deterministic list of reference-image paths (manifest declaration order). Person-excluding markers (`"icon"`, `"flat illustration"`, `"abstract pattern"`, `"logo"`, `"architectural detail"`, `"svg"`) short-circuit to `[]`. `include_people=None` falls back to `people_hint(prompt)` (negation like "no people"/"still life" → drop person slots; person word → force them; neither → manifest). Only person refs make an "identity scene" (gpt-image-2); dog-only refs go to flare.
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

In HTTP mode, `server.py::main()` calls `mcp.http_app(transport="streamable-http", stateless_http=True)` (FastMCP 4) and then `_mount_static_files(app)` mounts `/data/images` at `/`. This is what makes hosted URLs like `https://img.cdit-works.de/cdit/foo-1200x630.webp` resolve. The `/mcp` path is reserved for the MCP protocol. `mimetypes.add_type("image/webp"/".avif")` is needed because `python:3.12-slim` does not register them by default (see commit `406df0c`).

#### Gallery (Tailnet-only)

`server.py::_mount_gallery(app)` inserts a Starlette sub-app at `/gallery` **before** the root static mount, so the prefix wins routing. The sub-app's routes are:

- `GET  /gallery/`                  → vanilla JS shell (`gallery/static/index.html`)
- `GET  /gallery/static/<path>`     → CSS/JS/`fflate.min.js`
- `GET  /gallery/api/images`        → filtered + paged list (query: `brand`, `platform`, `from`, `to`, `q`, `min_width`, `min_height`, `sort`, `limit ≤ 500`, `offset`)
- `GET  /gallery/api/images/<path>` → single entry (deep links)
- `POST /gallery/api/reindex`       → synchronous rescan of `/data/images/**/*.json`

The index lives in memory (`gallery/index.py::GalleryIndex`), built by walking JSON sidecars. It's rebuilt on Starlette startup (blocking), on a background timer (`GALLERY_REINDEX_INTERVAL_SECONDS`, default 300), and on demand via the reindex endpoint. There is no database and no file watcher.

Auth is hostname-based: `gallery/middleware.py::TailnetOnlyMiddleware` rejects `/gallery/*` requests whose `Host` header doesn't match `GALLERY_TAILNET_HOSTNAME` with HTTP 404 (not 403 — don't advertise existence). The production hostname comes from the docktail `service.name` label in `compose.yaml` plus the `GALLERY_TAILNET_HOSTNAME` env. When the env var is unset, the middleware is a no-op and logs one startup WARN. Other paths (`/mcp`, `/<brand>/*.webp`) are never gated. The container is exposed on the Tailnet by docktail (Tailscale serve via labels) — no separate `tailscale serve` config required.

Bulk download is client-side: the frontend `fetch`es selected WebPs, feeds them to the vendored `fflate` (`gallery/static/fflate.min.js`, version pinned — see the neighboring `README.md` for SHA-256), and triggers a single Blob URL download. This is what makes it work on iOS Safari.

### Auth (HTTP mode only)

`auth.py::create_auth` returns a `MultiAuth` composed of:
- **OIDCProxy** for Keycloak (issuer and audience from `KEYCLOAK_*` settings) — this is the path Claude.ai connectors take. No DCR; credentials are pre-registered.
- **BearerTokenVerifier** for a static API key prefixed `bmcp_` — used by Claude Code, n8n, scripts.

Auth in HTTP mode is **fail-fast** (see commit `c637e42`): `_build_auth()` reads **only** `MCP_BILDSPRACHE_API_KEY` (the fleet's one sanctioned exception to `MCP_API_KEY`; the stack's `MCP_API_KEY` var is ignored) and raises `SystemExit` if it is unset, rather than silently running unauthenticated. `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD` add a Cloudflare Access JWT verifier alongside the bearer. If `KEYCLOAK_CLIENT_SECRET` is set, the server returns the full `MultiAuth` (Keycloak + bearer); if only the API key is set, the server returns a `BearerTokenVerifier` alone (the current production shape post-Keycloak-decommission).

Stdio mode skips auth entirely.

## fastmcp 4 idioms

- Fleet conventions (tag-only releases, bearer auth behind the Cloudflare portal, usage telemetry, job + poll): `CDiT-infrastructure/docs/wiki/topics/mcp-fleet.md`.
- Tests: the `mcp-testing` skill. Release/deploy workflow changes: the `cdit-release-pipeline` skill.
- Bearer env is `MCP_BILDSPRACHE_API_KEY`, not `MCP_API_KEY`. Rename both sides together or auth fails fast on deploy.
- Long renders use job + poll: 20 s inline wait (`SYNC_WAIT_SECONDS`), then `get_image_result`, whose `wait_seconds` is clamped to `POLL_WAIT_MAX_SECONDS` (20 s). The portal cuts responses at about 60 s and forwards no elicitation.
- The gallery is Tailnet-only (`TailnetOnlyMiddleware` + docktail); its index is in memory, rebuilt from sidecars.
- New tools or params need a Cloudflare portal catalog refresh before clients see them.

## Production deployment

Production runs the `compose.yaml` stack on a single Docker host, built from a git clone (`build: .`) on each deploy; the GHCR image from the release workflow is not used. Stored images are served from the public image domain (`IMAGE_DOMAIN`) and the MCP endpoint is `<public-host>/mcp`. `FASTMCP_HOME=/data/fastmcp` and two named volumes (`fastmcp-data`, `images-data`) persist state. `/health` reports the static `pyproject.toml` version (it lags the tags) plus `git_commit`, which a Dockerfile stage reads from the clone's `.git`; that commit is what identifies a deploy.

## Single source of truth

There is no longer a local `~/.claude/skills/bildsprache/` skill or an `install.sh` distribution path — this MCP server is the only way to reach Bildsprache. Brand visual presets live in `mcp_bildsprache/presets.py`; identity packs live on the `identity-data` volume; the AI-attribution contract is mirrored from `CaseyRo/CDiT-marketingskills/shared/` via `.github/workflows/shared-contract-check.yml`. When you change brand DNA, model routing, or sizing, edit it here and let CI ship — every client (Claude.ai, Claude Code, n8n) gets the change from the same server.
