# Tasks

## 1. Hotfix — unblock the event loop (no new deps, fixes CDI-1312)

- [ ] 1.1 `gallery/index.py::_reindex_loop` → `await asyncio.to_thread(index.refresh)`; offload the startup build walk too
- [ ] 1.2 `server.py` render path → wrap `process_image`, `store_image`, `store_raw_image` in `await asyncio.to_thread(...)`
- [ ] 1.3 Verify: the loop stays responsive during a render and a reindex tick; a real `gpt-image-2` render completes and is delivered or recoverable via `list_recent_generations`

## 2. Delete dead code

- [x] 2.1 Remove `providers/bfl.py`, `providers/recraft.py`, their tests. (KEPT `route_model`'s flux/recraft→`ProviderTemporarilyDisabled` rejection + `DISABLED_PROVIDERS` + the `Literal` entries on purpose — removing them changes the public schema / forces a portal catalog re-sync for ~15 lines.)
- [x] 2.2 Remove `FALLBACKS`, `REFERENCE_FALLBACKS`, and the cross-provider fallback try/except (always-`None`, never fired). Net −833 lines.
- [ ] 2.3 Merge `_render_image_job` + `_render_diagram_job` into one parametrized `_render_job` — DEFERRED: behavioral-refactor risk for ~150 lines; reassess after 2.1/2.2.

## 3. FastMCP-native tasks (design-gated)

- [ ] 3.1 Spike `@mcp.tool(task=True)` for `generate_image`/`generate_diagram` — confirm whether it needs `fastmcp[tasks]`+Docket(Redis) or runs in-process for a single container
- [ ] 3.2 If viable without mandatory Redis: replace `jobs.py`, `_dispatch_and_maybe_wait`, `get_image_result`, and the `background`/`job_id` params with native task dispatch + client polling; refresh the Cloudflare portal catalog
- [ ] 3.3 Drop the ledger's cross-worker `find_by_request_id` fallback if native task state covers recovery
- [ ] 3.4 If Redis is mandatory and unwanted: keep a minimal in-process detached task; delete only the redundant registry/dispatch wrappers

## 4. Validate

- [ ] 4.1 `uv run pytest -x` green; trim/repoint tests for the deleted providers + merged render path
- [ ] 4.2 Confirm CDI-1312 fixed end-to-end; update CDI-1253/1266/1312 accordingly
