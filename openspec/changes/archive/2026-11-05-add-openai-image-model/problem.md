# Problem: Bildsprache does not offer OpenAI's newest image model

Card: COS-5 · Project: mcp-fleet · Shape: feature · Domain: clear

## Background
Voice memo from Casey, 5 Nov 2026 14:00 (Just Press Record, Dutch, transcribed locally):

> "GPT heeft een nieuwe image model, 2.6. Ik denk dat we onze beeldsprachen-mcp-server kunnen ondersteunen om dit ook te ondersteunen."

OpenAI has released a new image model, which Casey calls "2.6", and he wants Bildsprache to support it. The name comes from speech and is not verified: neither the public model id nor the release is confirmed yet. The last lineup refresh was CDI-1265 (added GPT Image 1.5, Nano Banana Pro).

## Current state
Raster renders run on OpenAI's 2.5 generation: `server.py::_pick_raster_model` uses `gpt-image-2.5-sunburst` for identity scenes (Casey's bake-off pick, 15 Oct 2026) and `gpt-image-2.5-flare` otherwise; `OPENAI_IMAGE_MODEL` forces one, and `gpt-image-2` is still accepted as a hint. `gpt-image-1.5` and `gpt-image-1-mini` are retired hints mapped to `gpt-image-2`. Attribution, the outcome ledger and presets treat OpenAI models by family (`gpt-image`, `gpt-image-*`), so a new id may mostly flow through; the cost table, the `_ModelLiteral` and `list_models` need it named.

## Goal
A Bildsprache caller can choose OpenAI's newest image model by name, with the same attribution, ledger, cost and preset behaviour as the 2.5 models.

## Success criterion
- The new model appears in `list_models` and produces an image through `generate_image` against the live API (one real render).
- Attribution and the ledger record the exact model id.
- Existing tests stay green; one test covers the new id.
Assumed (clear card, nothing asked): the new model is added next to the 2.5 models, selectable per call, and `_pick_raster_model` does not change in this card.

## Appetite
3.

## Out of scope
- Changing which model `_pick_raster_model` picks (flare / sunburst came out of a bake-off; a switch to 2.6 deserves its own bake-off card).
- Prompt tuning, comparison runs, or retiring older models.
- Other providers.

## Done artifact
A merged PR adding the new OpenAI image model to mcp-bildsprache, with green tests and the model listed in `list_models`.

## Verdict
build. Riskiest assumption: that a model called "2.6" exists in OpenAI's image API under a stable id. Cheapest test before building: read OpenAI's current image model docs or the `/v1/models` list and confirm the exact id(s) and any new parameters; 2.5 shipped as two named variants (flare, sunburst), so 2.6 may too. If no such model exists, set the card to Needs me with what was found instead.
