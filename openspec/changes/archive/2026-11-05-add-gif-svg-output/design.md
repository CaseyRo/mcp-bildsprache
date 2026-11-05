# Design: GIF and SVG output on request

## Approach
`generate_image` gains `output` (default `raster`). For `gif` or `svg` the tool picks gpt-image-2.6-flare unless the caller named a 2.6 model, refuses any other model or provider, refuses caller reference images and does not attach identity-pack refs, then dispatches through the existing detached job (inline wait, then `get_image_result`). The OpenAI provider sends `output_format` gif or svg on `/images/generations` with `n=1`, no background and no compression; GIF adds `frame_count` / `frame_duration_ms` and a frame size with the long edge at most 512 px (1024 px frames do not fit OpenAI's sheet); SVG adds `svg: {model}` and a 300 s HTTP timeout (the render takes about 121 s, the old timeout was 120 s). The response's `svg_usage` rides along inside `ProviderResult.usage`.

The render job skips `process_image` for GIF and SVG and stores the bytes as returned with the right extension. `attribution` prices the image tokens as before and, when `svg_usage` is present, the author model's tokens from its own cost-table row, summed into one `amount_eur`.

## Touches
| System or repo | Change |
| --- | --- |
| mcp-bildsprache `server.py` | `output`, `frame_count`, `frame_duration_ms` on `generate_image`; validation; skip WebP for gif/svg; ledger `output`; SVG CSP on the static mount; `list_models` text |
| `providers/openai.py` | gif/svg request shape, mime types, GIF frame size, SVG timeout, `svg_usage` |
| `attribution.py`, `_shared/provider_costs.v1.yaml` | SVG author cost; `gpt-5.5` row ($5 in, $0.50 cached, $30 out per 1M, OpenAI pricing page 5 Nov 2026) |
| `storage.py`, `slugs.py`, `config.py` | file extension; `OPENAI_SVG_MODEL` (default `gpt-5.5`) |

## Decisions taken
- SVG safety by header, not by rewriting the markup: every `.svg` from the static mount gets `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox` and `nosniff`. It blocks scripts and external loads whatever the model wrote, and cannot break a drawing.
- No reference images with GIF or SVG (SVG has no edit endpoint; GIF on edits is unconfirmed).
- `raw` has no effect for GIF and SVG: the stored file already is the provider original.
- Reasoning tokens of the SVG author are billed once: if `total_tokens` exceeds input plus output, the remainder is billed at the output rate.

## Decisions deferred
- Pre-call cost estimate for GIF and SVG (the elicitation estimate still uses the still-image figure); Casey, if the confirm prompt ever reaches him.
- Mirroring the `gpt-5.5` row into CDiT-marketingskills/shared: monthly CDiT OS update.

## Risks
- Non-square GIF frame sizes are unverified live; the API answers with a clear 400 if a sheet does not fit. Square 512 is known-good.
- One SVG holds the OpenAI dispatch lock for about 2 minutes; other OpenAI renders queue behind it.
