# Simplify image render: unblock the loop, adopt FastMCP-native tasks, delete dead code

## Why

Image generation is effectively dead — no successful render since 2026-06-18 (CDI-1312). Root cause: the single asyncio event loop is starved by synchronous work that is never offloaded:

- the gallery reindex (`rglob` + JSON-parse of the whole image volume) runs on the loop every 300s and at startup (`gallery/index.py::_reindex_loop` → `index.refresh()`, comment: "Refresh is quick; run on the event-loop thread" — no longer true at 210 artifacts), and
- per-render `process_image` (PIL) and `store_image`/`store_raw_image` (disk) run on the loop (`server.py`).

When the loop is frozen, **every** MCP call returns `-32001` — including the `background:true` dispatch that should return instantly — and scheduled render tasks never run (no artifact, no ledger record). That is CDI-1312 exactly. CDI-1253/1266 (portal/session timeouts) are real but secondary.

Compounding the fragility, the server hand-rolled ~500 lines of async-dispatch machinery (`jobs.py` `JobRegistry` + `spawn_detached`, `_dispatch_and_maybe_wait`, `get_image_result`, the ledger cross-worker fallback, `background`/`job_id` params on two tools) that reinvents FastMCP 3.x's native `@mcp.tool(task=True)` background tasks. And the May 2026 brand collapse left dead code: FLUX/Recraft providers disabled at the dispatcher but still in-tree (~400 lines), plus `FALLBACKS`/`REFERENCE_FALLBACKS` that are byte-identical and map every provider to `None` (a no-op fallback system).

## What Changes

- **Unblock the event loop (fixes the outage, zero new deps):** offload `process_image`, `store_image`/`store_raw_image`, and the gallery reindex walk to threads via `asyncio.to_thread`. (FastMCP already auto-threadpools sync `@mcp.tool` functions; only the blocking helpers inside async coroutines need explicit offload.)
- **Adopt FastMCP-native background tasks** for `generate_image`/`generate_diagram`, replacing `jobs.py` + `_dispatch_and_maybe_wait` + `get_image_result` + the `background`/`job_id` plumbing — gated on the design decision below (in-process vs `fastmcp[tasks]`/Docket).
- **Delete dead code:** `providers/bfl.py`, `providers/recraft.py`, `DISABLED_PROVIDERS` and the disabled-provider routing branch; `FALLBACKS`/`REFERENCE_FALLBACKS` and the never-firing cross-provider fallback try/except.
- **Dedup the render path:** merge `_render_image_job` and `_render_diagram_job` into one parametrized `_render_job`.
- **Shrink the ledger** by dropping the cross-worker `find_by_request_id` fallback once native task state owns recovery.

## Non-goals / Impact

- The portal aggregator's hard ~60s budget (CDI-1266) is a separate infra fix; native tasks make the client poll instead of wait, sidestepping it.
- `get_image_result` is published through the Cloudflare MCP portal — removing/renaming it needs a portal catalog refresh.
- Re-enabling FLUX/Recraft later = restore from git history (one PR), not kept as dead weight in-tree.
- **Design decision (Phase 3):** whether `@mcp.tool(task=True)` runs in-process for this single container or mandates a Redis/Docket backend (`fastmcp[tasks]`). If Redis is unwanted, keep a minimal in-process detached task and delete only the redundant wrappers.
- Net: ~-1,100 lines. Deps: net ~0 (possibly +`fastmcp[tasks]` extra).
