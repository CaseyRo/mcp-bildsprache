# mcp-bildsprache

An MCP server for brand-aware image generation. It takes a prompt and a brand context, injects that brand's visual preset (palette, mood, composition rules), renders the image with OpenAI or Google Gemini, then resizes, converts to WebP, embeds provenance metadata, and stores the result under a hosted URL. It also renders flow, sequence and state diagrams from free text or Mermaid. It is built for a small set of brands owned by one studio, so the presets are specific to them; the pipeline, job handling and gallery are reusable if you swap in your own presets. Built on [FastMCP](https://gofastmcp.com) 4.

## Requirements

- Python 3.11 or newer
- FastMCP 4 (`fastmcp>=4.0.10,<5.0.0`, installed as a dependency)
- An OpenAI API key (raster images) and a Google Gemini API key (diagrams). A missing key disables that provider; the server still starts.

## Install and run

### Local

```bash
uv sync
uv run mcp-bildsprache                    # stdio, for a local MCP client

OPENAI_API_KEY=... GEMINI_API_KEY=... MCP_BILDSPRACHE_API_KEY=change-me \
  IMAGE_STORAGE_PATH=./data/images TRANSPORT=http uv run mcp-bildsprache
```

In HTTP mode the MCP endpoint is `/mcp`, stored images are served from `/`, and `/health` reports the version and git commit.

### Docker

The image builds from source:

```bash
docker compose up -d --build
```

`compose.yaml` keeps state in three named volumes: `images-data` (generated images, sidecars and the outcome ledger), `fastmcp-data`, and `identity-data` (optional reference images, mounted read-only). Its defaults are tuned for the author's deployment; override the environment variables below for your own.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | empty | OpenAI key for raster images |
| `GEMINI_API_KEY` | empty | Gemini key for diagrams, and raster by explicit hint |
| `OPENAI_IMAGE_MODEL` | empty | Force one OpenAI model; empty lets the server choose per call |
| `TRANSPORT` | `stdio` | `stdio` or `http` (the Docker image uses `http`) |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address and port |
| `MCP_BILDSPRACHE_API_KEY` | none | Bearer token for HTTP mode. This server uses this name instead of `MCP_API_KEY`. |
| `MCP_BILDSPRACHE_PUBLIC_URL` | empty | Public origin, used in OAuth metadata |
| `IMAGE_DOMAIN` | the author's image host | Base URL for `hosted_url`; point it at wherever `/` of this server is published |
| `IMAGE_STORAGE_PATH` | `/data/images` | Where images and JSON sidecars are written |
| `LEDGER_ENABLED` / `LEDGER_PATH` | `true` / `<IMAGE_STORAGE_PATH>/_ledger/generations.jsonl` | Append-only outcome ledger, one line per generation attempt |
| `SYNC_WAIT_SECONDS` | `20` | Inline wait before a render is handed back as a job (0 always returns a job) |
| `POLL_WAIT_MAX_SECONDS` | `20` | Ceiling for `get_image_result(wait_seconds=...)` |
| `IDENTITY_ENABLED` / `IDENTITY_DIR` | `true` / `/data/identity` | Optional reference-image packs, see below |
| `CF_ACCESS_TEAM_DOMAIN` / `CF_ACCESS_AUD` | empty | Optional Cloudflare Access JWT verification |
| `KEYCLOAK_ISSUER`, `KEYCLOAK_AUDIENCE`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_CLIENT_SECRET` | see `config.py` | Optional OIDC login, active only when the client secret is set |
| `GALLERY_ENABLED` | `true` | Mount the browse UI at `/gallery` |
| `GALLERY_TAILNET_HOSTNAME` | unset | Only requests with this `Host` header may reach `/gallery`; unset means no host check |
| `GALLERY_REINDEX_INTERVAL_SECONDS` | `300` | How often the gallery index is rebuilt from sidecars |

## Authentication

Stdio mode has no auth. HTTP mode refuses to start without `MCP_BILDSPRACHE_API_KEY`, and MCP requests must send `Authorization: Bearer <key>`. Two optional verifiers can be added: a Cloudflare Access JWT verifier (when `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` are set) and an OIDC proxy (when `KEYCLOAK_CLIENT_SECRET` is set). Stored images under `/` are public by design, because they are meant to be embedded.

## Tools

| Tool | What it does |
| --- | --- |
| `generate_image` | Render an image with the brand preset for `context`, sized for `platform` or explicit `dimensions`. Optional `model`, `quality`, `transparent`, `register`, `reference_images`, `raw`, `background`. Returns a result or a job handle. |
| `generate_diagram` | Render a flow, sequence or state diagram from `prompt` or `mermaid` source. Gemini by default, OpenAI with `model_hint="openai"`. Returns a result or a job handle. |
| `get_image_result` | Fetch or long-poll the result of a pending render by `job_id`. Reads local state only. |
| `generate_prompt` | Return the engineered prompt without calling a provider |
| `list_models` | Active providers and models, disabled providers, diagram support, whether reference packs are loaded |
| `get_visual_presets` | Brand presets, optionally filtered by `context` and `register` |
| `list_recent_generations` | Newest generated images from the on-disk index, to recover a result whose response was lost |
| `generation_stats` | Per-model success and failure counts from the outcome ledger |

Resources: `bildsprache://presets`, `bildsprache://palette/casey`, `bildsprache://platforms`, `bildsprache://models`, `bildsprache://status`. Prompts: `brand_image_brief`, `mermaid_to_diagram`.

Providers: OpenAI (gpt-image-2 and the gpt-image-2.5 models) for raster, Gemini (Nano Banana Pro) for diagrams. There is no automatic fallback between providers; a provider error fails the job. A failure within the inline wait surfaces as an MCP tool error; a failure after that is reported by `get_image_result` as `status: error`.

### Long renders: job and poll

Some renders take 50 to 80 seconds, longer than many proxies hold a request open. `generate_image` and `generate_diagram` therefore start the render in the background and wait up to `SYNC_WAIT_SECONDS` (20 s):

1. A render that finishes in time returns its result inline, including `hosted_url`.
2. A slower one returns `{job_id, status: "pending", poll_with: "get_image_result"}` while the render continues.
3. Call `get_image_result(job_id, wait_seconds=20)` until `status` is `done` (with `hosted_url`) or `error`. Unknown ids return `not_found`.

Pass `background=true` to always get the job handle immediately. Results are also written to a durable ledger, so a `job_id` still resolves after a restart.

### Usage telemetry

A small middleware (`usage.py`) writes one JSON line per tool call to stderr with the server name, tool name, duration, outcome and protocol version. It never logs arguments or results.

## Brands and reference images

Brand presets live in `mcp_bildsprache/presets.py`. `get_visual_presets` and `list_models` report what is configured. To use the server for your own brand, edit the presets and slug prefixes there.

A brand can optionally have an identity pack: a manifest plus reference images, kept on a private volume and never committed. When present, matching references are sent to the provider so recurring people or subjects stay recognizable. See [docs/identity/README.md](docs/identity/README.md) for the manifest format.

## Gallery

In HTTP mode the server can serve a small browser UI at `/gallery/` to browse, filter, search and bulk-download generated images. Its index is built in memory from the JSON sidecars. The gallery has no login of its own: expose it only on a private network, and set `GALLERY_TAILNET_HOSTNAME` (any private hostname) so requests arriving under another host get a 404.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
```

CI (`.github/workflows/ci.yml`) runs the tests as the `test` check on every pull request. `main` is protected and changes land through pull requests.

## Releases

Releases are tag-only. After a merge to `main`, the release workflow tests the code and pushes the next `v*` patch tag; nothing is committed back to `main`, so the `version` in `pyproject.toml` lags the tags. Deployments build the Docker image from source, and `/health` reports the deployed git commit.

## Support

If this server saves you time, you can [buy me a coffee](https://buymeacoffee.com/caseyberlin).

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Casey Romkes.
