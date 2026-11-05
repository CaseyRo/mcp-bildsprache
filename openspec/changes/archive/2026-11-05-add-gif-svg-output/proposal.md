# Proposal: GIF and SVG output on request (GPT Image 2.6 only)

## Why
See problem.md: GPT Image 2.6 makes animated GIFs and SVGs, Bildsprache can only return WebP, and Casey wants both available on request because they cost more.

## Solution form
code, in mcp-bildsprache: the capability is an MCP tool parameter, so it has to live in the server.

## What changes
- `generate_image` takes `output: "raster" | "gif" | "svg"` (default `raster`), plus `frame_count` and `frame_duration_ms` for GIF.
- GIF and SVG run on gpt-image-2.6-flare (default) or -sunburst only; anything else is a clear error.
- GIF and SVG skip the WebP pipeline and are stored as returned (`.gif`, `.svg`), served from the same static mount; SVG responses carry a script-blocking Content-Security-Policy.
- `ai_attribution` prices the SVG author's tokens on top of the image tokens; the cost table gets a `gpt-5.5` row. The ledger records the format.
- `list_models` and the tool docstring state: on request only, costs more.

## What does not change
Raster calls, model defaults, `edit_image`, `generate_diagram`, Gemini. No reference images with GIF or SVG.

## Done artifact
A merged PR in mcp-bildsprache: `generate_image` returns a hosted GIF or SVG when asked, with 2.6 models only, priced in `ai_attribution`; one live render of each.
