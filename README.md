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

- `generate_image` — Full image generation with brand preset injection
- `generate_prompt` — Prompt engineering only (no image generation)
- `list_models` — Available models and their capabilities
- `get_brand_presets` — Brand visual presets for each context

## Authentication

Dual auth via MultiAuth:
- **Keycloak JWT** for Claude.ai connectors
- **Bearer token** (`bmcp_` prefix) for Claude Code, n8n, scripts
