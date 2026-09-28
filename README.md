# mcp-bildsprache

MCP server for brand-aware image generation, built on FastMCP 4. Providers: OpenAI (gpt-image-2, gpt-image-2.5-flare, gpt-image-2.5-sunburst) for raster, Google Gemini (Nano Banana Pro) for diagrams; Nano Banana 2 by explicit hint. There is no automatic provider fallback. FLUX and Recraft were deleted in 2026-06; hinting at them returns `PROVIDER_TEMPORARILY_DISABLED`.

## Quick Start

```bash
# Local development
pip install -e .
OPENAI_API_KEY=... GEMINI_API_KEY=... MCP_BILDSPRACHE_API_KEY=... TRANSPORT=http mcp-bildsprache

# Docker
docker compose up --build
```

## MCP Tools

- `generate_image` — Full image generation with brand preset injection.
  Default raster path: OpenAI at medium quality: gpt-image-2 for identity scenes, gpt-image-2.5-flare otherwise (`OPENAI_IMAGE_MODEL` forces one). Per call,
  `model` picks `gpt-image-2.5-flare` (fast) or `gpt-image-2.5-sunburst` (premium/editing);
  `quality` (`low`…`high`, `auto`; `xhigh`/`max` on 2.5) and `transparent` (2.5 only) are
  OpenAI options. The retired `gpt-image-1.5` / `gpt-image-1-mini` hints map to gpt-image-2.
  Optional `register: 'personal' | 'professional'` for the casey brand (May 2026 brand
  collapse). Optional `reference_images: list[bytes]` forwards reference images to OpenAI
  (or auto-resolves from the brand's identity pack when `context` is set). Optional
  `include_dogs: bool | None` overrides the dog-slot heuristic for casey (True =
  force-include Sien + Fimme, False = suppress, None = use manifest rules). Optional
  `include_people: bool | None` does the same for Casey's person refs; None reads the
  prompt ("no people"/"still life"/"keine Personen" → none, a person word → refs, neither →
  manifest rules). Only person refs route the default model to gpt-image-2; else flare.
  **Async dispatch+poll (CDI-1266):** the response is a UNION — a fast render returns the
  `hosted_url` inline as before; a slow render (gpt-image-2 / Nano-Banana-Pro take 50-80s,
  past the ~60s Cloudflare-portal timeout) returns `{job_id, status: "pending", poll_with:
  "get_image_result"}` immediately while the render keeps running server-side. The render
  is detached from the request scope so it survives the portal teardown. Inline-wait budget
  is `SYNC_WAIT_SECONDS` (default 20s, under the portal limit); pass `background=true` (or
  set `SYNC_WAIT_SECONDS=0`) to always get the job handle. Poll with `get_image_result`.
- `generate_diagram` — Flow / sequence / state diagrams via Gemini Nano Banana Pro
  (`gemini-3-pro-image-preview`, default — top editing/control + 4K brand graphics) or
  OpenAI gpt-image-2 (`model_hint='openai'`). Accepts free-text `prompt` OR Mermaid
  `mermaid` source (parsed into a structured render brief). Brand palette + UML
  conventions injected automatically. Format scope: `flow`, `sequence`, `state`. Same async
  dispatch+poll response union as `generate_image` (`background=true` for an immediate
  job handle).
- `get_image_result` — (CDI-1266) Retrieve (or long-poll for) the result of an
  async render dispatched by `generate_image` / `generate_diagram`. Pass the `job_id` from
  the pending handle; returns `{status: pending | done | error | not_found, hosted_url?,
  ...}`. Optional `wait_seconds` long-polls up to a safe ceiling (`POLL_WAIT_MAX_SECONDS`,
  default 20s, under the portal limit) before returning. Resolves from the in-process job
  registry first, then falls back to the durable CDI-1264 ledger by `request_id == job_id`
  so results survive a container restart / different worker. Reads only local state — no
  provider call, no cost.
- `generate_prompt` — Prompt engineering only (no image generation).
- `list_models` — Active providers (`openai`: gpt-image-2 and the 2.5 models;
  `gemini`: Nano Banana Pro + Nano Banana 2) plus a `disabled_providers` array
  (`bfl`, `recraft` — rejected at the dispatcher; the modules were deleted in 2026-06). Also reports `identity_packs: {brand: bool}` and
  `diagram_capable: [...]` / `diagram_formats: [...]`.
- `get_visual_presets` — Brand visual presets for each context. Active brands:
  `casey` (with `personal` and `professional` register overlays), `yorizon`. Per-brand
  responses include `identity_pack_loaded: bool` and the matching register overlay
  when `register` is supplied.
- `list_recent_generations` — List the most recently generated artifacts (newest first),
  reading the on-disk sidecar index. Broad recovery path when a render's response was lost
  to a portal timeout and you don't have the `job_id`. Optional `brand` / `limit` / `offset`.
- `generation_stats` — Per-model success/failure stats from the durable CDI-1264 outcome
  ledger (success AND failure attempts) over a time window. Reads local JSONL only.

## Portal refresh

The Cloudflare MCP portal does not refresh its tool catalog from upstream. A new tool or
parameter is invisible through the portal until the catalog is refreshed there.

## Brands and registers

Active brands (May 2026 brand collapse): **casey**, **yorizon**.

The `casey` brand carries one shared visual DNA across two registers:

- `personal` — recognition surface, warmer kitchen-table mood, more bone, lower contrast.
- `professional` — verification surface, crisper schematic clarity, more white space.

Locked botanical palette: paper bone `#F4EFE3` (background, ~70%), forest moss
`#2C4A38` (primary), pine ink `#1F2E26` (text), weathered ochre `#B8884A` (accent ≤5%),
soft moss `#C7CFB8` (hairlines). Vollkorn-style typography. No all-caps anywhere.

Legacy brand keys (`casey-berlin`, `cdit-works`, `casey.berlin`, `@cdit`,
`storykeep`, `nah`) all normalise to `casey`. Yorizon is fully isolated (no shared
palette tokens). FLUX and Recraft are gone;
hinting at them returns `PROVIDER_TEMPORARILY_DISABLED` with a migration message.

## Identity packs

Personal-likeness reference images for brands like `@casey.berlin` live on
a private Docker volume (`identity-data` → `/data/identity/`). See
[`docs/identity/README.md`](docs/identity/README.md) for the volume
contract and example manifest.

## Authentication

HTTP mode refuses to start without `MCP_BILDSPRACHE_API_KEY` (this server's name for the
fleet's `MCP_API_KEY`). Accepted credentials:
- **Bearer token** (`bmcp_` prefix): the Cloudflare MCP portal, Claude Code, n8n, scripts
- **Cloudflare Access JWT** when `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD` are set
- **Keycloak OIDC** (via OIDCProxy) only if `KEYCLOAK_CLIENT_SECRET` is set; production runs without it

## Gallery (Tailnet-only)

Browse every generated image, filter/search, and download in bulk at the
internal gallery hostname (e.g. `https://bildsprache-gallery.<tailnet>.ts.net/gallery/`).
The public `/mcp` endpoint and `https://img.cdit-works.de/<brand>/*.webp`
static routes are unchanged. See `CLAUDE.md` → *Gallery* for details.
