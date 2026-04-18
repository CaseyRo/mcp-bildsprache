# mcp-bildsprache

MCP server for brand-aware image generation. Routes to Gemini, FLUX.2 Pro, or Recraft V3 based on brand context and content type.

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
  Optional `reference_images: list[bytes]` forwards reference images to
  providers that support them (Gemini, FLUX kontext-pro); usually
  auto-resolved from the brand's identity pack when `context` is set.
  Optional `include_dogs: bool | None` overrides the dog-slot heuristic
  for `@casey.berlin` (True = force-include, False = suppress,
  None = use manifest rules).
- `generate_prompt` — Prompt engineering only (no image generation)
- `list_models` — Available models and their capabilities. Also reports
  `identity_packs: {brand: bool}` so callers can see which brands have
  an identity pack loaded.
- `get_brand_presets` — Brand visual presets for each context. Per-brand
  responses include `identity_pack_loaded: bool`.

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
