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
- `generate_diagram` — Flow / sequence / state diagrams via Gemini Nano Banana Pro
  (`gemini-3-pro-image-preview`, default — top editing/control + 4K brand graphics) or
  OpenAI gpt-image-2 (`model_hint='openai'`). Accepts free-text `prompt` OR Mermaid
  `mermaid` source (parsed into a structured render brief). Brand palette + UML
  conventions injected automatically. Format scope: `flow`, `sequence`, `state`.
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
