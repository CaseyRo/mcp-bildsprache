# mcp-bildsprache

MCP server for brand-aware image generation. Active providers: OpenAI (gpt-image-2 + gpt-image-1.5) for raster, Google Gemini (Nano Banana Pro / Nano Banana 2) for diagrams and the raster fallback. FLUX.2 and Recraft V4.1 remain in-tree but disabled at the dispatcher (re-enabling is a one-PR swap).

## Quick Start

```bash
# Local development
pip install -e .
GEMINI_API_KEY=... BFL_API_KEY=... RECRAFT_API_KEY=... TRANSPORT=http mcp-bildsprache

# Docker
docker compose up --build
```

## MCP Tools

- `generate_image` — Full image generation with brand preset injection.
  Default raster path: OpenAI gpt-image-2; `model_hint='gpt-image-1.5'` selects the
  quality sibling GPT Image 1.5 (high) using the same image params (size/quality tiers).
  Optional `register: 'personal' | 'professional'` for the casey brand (May 2026 brand
  collapse). Optional `reference_images: list[bytes]` forwards reference images to OpenAI
  (or auto-resolves from the brand's identity pack when `context` is set). Optional
  `include_dogs: bool | None` overrides the dog-slot heuristic for casey (True =
  force-include Sien + Fimme, False = suppress, None = use manifest rules).
  **Async dispatch+poll (CDI-1266):** the response is a UNION — a fast render returns the
  `hosted_url` inline as before; a slow render (gpt-image-2 / Nano-Banana-Pro take 50-80s,
  past the ~60s Cloudflare-portal timeout) returns `{job_id, status: "pending", poll_with:
  "get_image_result"}` immediately while the render keeps running server-side. The render
  is detached from the request scope so it survives the portal teardown. Inline-wait budget
  is `SYNC_WAIT_SECONDS` (default 40s, under the portal limit); pass `background=true` (or
  set `SYNC_WAIT_SECONDS=0`) to always get the job handle. Poll with `get_image_result`.
- `generate_diagram` — Flow / sequence / state diagrams via Gemini Nano Banana Pro
  (`gemini-3-pro-image-preview`, default — top editing/control + 4K brand graphics) or
  OpenAI gpt-image-2 (`model_hint='openai'`). Accepts free-text `prompt` OR Mermaid
  `mermaid` source (parsed into a structured render brief). Brand palette + UML
  conventions injected automatically. Format scope: `flow`, `sequence`, `state`. Same async
  dispatch+poll response union as `generate_image` (`background=true` for an immediate
  job handle).
- `get_image_result` — **NEW (CDI-1266).** Retrieve (or long-poll for) the result of an
  async render dispatched by `generate_image` / `generate_diagram`. Pass the `job_id` from
  the pending handle; returns `{status: pending | done | error | not_found, hosted_url?,
  ...}`. Optional `wait_seconds` long-polls up to a safe ceiling (`POLL_WAIT_MAX_SECONDS`,
  default 55s, under the portal limit) before returning. Resolves from the in-process job
  registry first, then falls back to the durable CDI-1264 ledger by `request_id == job_id`
  so results survive a container restart / different worker. Reads only local state — no
  provider call, no cost. **Requires a Cloudflare-portal catalog refresh before it is
  callable through the portal** (see "Portal refresh" below).
- `generate_prompt` — Prompt engineering only (no image generation).
- `list_models` — Active providers (`openai`: gpt-image-2 + gpt-image-1.5 + draft;
  `gemini`: Nano Banana Pro + Nano Banana 2) plus a `disabled_providers` array
  (`bfl`, `recraft` — modules in-tree but disabled at the dispatcher per the May 2026
  brand collapse). Also reports `identity_packs: {brand: bool}` and
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

## Portal refresh (new tool: `get_image_result`)

`get_image_result` (CDI-1266) is a **NEW tool**. The Cloudflare MCP portal does not
auto-refresh its tool catalog from upstream, so until the portal catalog is refreshed the
new tool is invisible/uncallable *through the portal* even after this server is deployed.
The async dispatch+poll flow degrades gracefully in the meantime: `generate_image` /
`generate_diagram` still return the `{job_id, status: "pending"}` handle (and fast renders
still return inline), and the completed artifact remains recoverable via the *existing*
`list_recent_generations` tool. Once the portal catalog is refreshed, the `job_id` →
`get_image_result` poll loop becomes available end-to-end.

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
palette tokens). FLUX and Recraft providers are temporarily disabled at the dispatcher;
hinting at them returns `PROVIDER_TEMPORARILY_DISABLED` with a migration message.

## Identity packs

Personal-likeness reference images for brands like `@casey.berlin` live on
a private Docker volume (`identity-data` → `/data/identity/`). See
[`docs/identity/README.md`](docs/identity/README.md) for the volume
contract and example manifest.

## Authentication

Dual auth via MultiAuth:
- **Keycloak JWT** for Claude.ai connectors
- **Bearer token** (`bmcp_` prefix) for Claude Code, n8n, scripts

## Gallery (Tailnet-only)

Browse every generated image, filter/search, and download in bulk at the
internal gallery hostname (e.g. `https://bildsprache-gallery.<tailnet>.ts.net/gallery/`).
The public `/mcp` endpoint and `https://img.cdit-works.de/<brand>/*.webp`
static routes are unchanged. See `CLAUDE.md` → *Gallery* for details.
