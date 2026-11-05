# Proposal: Offer OpenAI's GPT Image 2.6 models in Bildsprache

## Why
OpenAI released GPT Image 2.6 on 4 Nov 2026 and Bildsprache cannot select it (problem.md). The riskiest assumption held: `/v1/models` lists `gpt-image-2.6-flare` and `gpt-image-2.6-sunburst` (checked 5 Nov 2026 with the Bildsprache key; no dated snapshots yet).

## Solution form
code: a model id is a code and config change in mcp-bildsprache.

## What changes
- mcp-bildsprache: `gpt-image-2.6-flare` and `gpt-image-2.6-sunburst` become selectable per call (`model=`), with the same quality tiers (up to `xhigh` / `max`) and transparent backgrounds as the 2.5 models.
- The cost table gets a row for each, so attribution and the ledger price them.
- `list_models` lists them.

## What does not change
- `_pick_raster_model`: the default split stays on the 2.5 models until a bake-off says otherwise.
- The 2.6 extras (GIF output, SVG export, markup-guided edits) are not wired.
- Other providers.

## Done artifact
A merged PR adding the new OpenAI image model to mcp-bildsprache, with green tests and the model listed in `list_models`.
