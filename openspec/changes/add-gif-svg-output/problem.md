# Problem: Bildsprache cannot hand out an animated GIF or an SVG, though GPT Image 2.6 can make both

Card: COS-21 · Project: mcp-fleet · Shape: feature · Domain: clear

## Background
Capture, conversation 5 Nov 2026 16:06, after the 2.6 bake-off:

> lets add SVG+GIF support for bildsprache (But only on request, its more expensive)

The bake-off called `/v1/images/generations` directly:
- GIF: `output_format: "gif"`, `frame_count` (API reference: 2 to 16, default 9), `frame_duration_ms` (min 20, default 200). 9 frames at 200 ms, 512 by 512: EUR 0.058, Flare 11 s, Sunburst 26 s. 1024 by 1024 frames do not fit OpenAI's frame sheet.
- SVG: `output_format: "svg"` plus a required `svg.model` (the text model that writes the SVG from a reference picture; `gpt-5.5` worked), `n` must be 1, no edits. EUR 0.28 (Flare), 0.22 (Sunburst), about 121 s. The response carries a separate `svg_usage` block for the text model.

## Current state
`generate_image` always returns a WebP: every result goes through resize, crop and WebP conversion, and storage only writes `.webp`. The OpenAI provider has a 120 s HTTP timeout, which a 121 s SVG would hit. `ai_attribution` prices image tokens only; there is no row for a text model.

## Goal
A caller who asks for a GIF or an SVG gets a hosted file in that format, priced correctly; nobody else sees a change.

## Success criterion
One live GIF and one live SVG through the Bildsprache MCP tool, each with a hosted URL, a cost in `ai_attribution` that matches the bake-off order of magnitude, and the format in the ledger. Existing raster calls unchanged (full test suite green).

## Appetite
3 (classifier estimate; the capture agrees).

## Out of scope
- GIF or SVG from `edit_image`, `generate_diagram` or Gemini.
- Reference images (identity packs) with GIF or SVG: SVG has no edit endpoint and GIF on edits is unconfirmed.
- Upscaling GIFs beyond the frame size the API returns.
- Choosing GIF or SVG automatically.

## Done artifact
A merged PR in mcp-bildsprache: `generate_image` returns a hosted GIF or SVG when asked, with 2.6 models only, priced in `ai_attribution`; one live render of each.

## Verdict
build. Clear card, nothing asked; assumptions: GIF frames capped at 512 px on the long edge (the known-good size), SVG author `gpt-5.5` by default, settable in config. Riskiest assumption: the SVG render (~2 min) survives the portal; it rides the existing job and poll path, tested live.
