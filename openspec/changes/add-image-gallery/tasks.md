## 1. Package scaffold

- [x] 1.1 Create `mcp_bildsprache/gallery/__init__.py` (package marker)
- [x] 1.2 Create empty `mcp_bildsprache/gallery/app.py`, `index.py`, `middleware.py`
- [x] 1.3 Create `mcp_bildsprache/gallery/static/` directory; add `.gitkeep`
- [x] 1.4 Add gallery settings to `mcp_bildsprache/config.py`: `gallery_enabled: bool = True`, `gallery_reindex_interval_seconds: int = 300`, `gallery_tailnet_hostname: str | None = None`, `gallery_soft_zip_cap_mb: int = 250`

## 2. Index (sidecar scanner)

- [ ] 2.1 Define `GalleryEntry` dataclass in `gallery/index.py` with all fields from the design doc (path, hosted_url, brand, prompt, prompt_lower, model, cost_estimate, width, height, platform, file_size, created_at)
- [ ] 2.2 Implement `scan_index(data_dir: Path, public_base_url: str) -> list[GalleryEntry]` — walk `<data_dir>/**/*.json`, parse each sidecar, build entries, tolerate missing optional fields, skip WebPs without sidecars
- [ ] 2.3 Derive `brand` from the top-level dir under `data_dir`
- [ ] 2.4 Derive `created_at` from sidecar `created_at` if present, else `Path.stat().st_mtime`
- [ ] 2.5 Implement `GalleryIndex` class holding the entry list + path-keyed dict, with `refresh()`, `total()`, `get(path)`, and `filter_and_sort(**query)` methods
- [ ] 2.6 Implement filtering: `brand` (list), `platform` (exact), `from`/`to` (ISO-8601 → datetime), `q` (substring over `prompt_lower`), `min_width`, `min_height`
- [ ] 2.7 Implement sorting: `created_desc` (default), `created_asc`, `cost_desc`, `size_desc`
- [ ] 2.8 Implement pagination: `limit` (default 100, max 500), `offset` (default 0)

## 3. Background reindex loop

- [ ] 3.1 Implement `async def _reindex_loop(index, interval_s)` in `gallery/index.py`
- [ ] 3.2 Log `INFO` on each successful reindex with count + duration; `WARN` on failures without crashing the loop

## 4. Starlette sub-app

- [ ] 4.1 In `gallery/app.py`, build a Starlette `Router` with: `GET /` (HTML shell), `GET /static/{path:path}` (static files), `GET /api/images`, `GET /api/images/{path:path}`, `POST /api/reindex`
- [ ] 4.2 Implement HTML-shell handler that returns `gallery/static/index.html`
- [ ] 4.3 Implement `GET /api/images` handler parsing query params, calling `index.filter_and_sort`, returning `{total, limit, offset, items}`
- [ ] 4.4 Implement `GET /api/images/{path}` handler returning 404 on miss
- [ ] 4.5 Implement `POST /api/reindex` handler performing synchronous refresh and returning `{total}`
- [ ] 4.6 Wire Starlette `Lifespan`: first scan synchronously, then spawn reindex task; cancel on shutdown

## 5. Tailnet-only middleware

- [ ] 5.1 Implement `TailnetOnlyMiddleware` in `gallery/middleware.py`: rejects `/gallery/*` requests whose `Host` header doesn't match the configured Tailnet hostname
- [ ] 5.2 Configurable: if `gallery_tailnet_hostname` is unset, middleware is a no-op (dev convenience) and logs a single startup WARN
- [ ] 5.3 Rejection response is `HTTP 404` (not 403) to avoid advertising existence

## 6. Mount into the existing HTTP app

- [ ] 6.1 In `server.py::main()`, after `mcp.http_app(transport="http")` returns the ASGI app, mount the gallery sub-app at `/gallery` if `settings.gallery_enabled`
- [ ] 6.2 Install `TailnetOnlyMiddleware` on the parent app so it fires for any `/gallery/*` path
- [ ] 6.3 Confirm ordering: static `/data/images` stays mounted at `/`, `/mcp` still routes to FastMCP, `/gallery` is new

## 7. Frontend — static assets

- [ ] 7.1 Vendor `fflate.min.js` into `gallery/static/` (pinned version, checksum noted in a neighboring `README.md`)
- [ ] 7.2 Write `gallery/static/index.html` — single page with filter bar, view toggle, selection counter, download button, grid/list region
- [ ] 7.3 Write `gallery/static/styles.css` — grid + list layouts, filter bar, selection highlight, disabled-button state; CSS custom properties for theme tokens
- [ ] 7.4 Write `gallery/static/app.js` — `GalleryState` object, URL-query-string sync, debounced search input (≤250 ms), API fetch, render
- [ ] 7.5 Implement grid view rendering with `loading="lazy"` and `decoding="async"` on each `<img>`
- [ ] 7.6 Implement list view rendering: table with columns prompt / brand / date / dimensions / model / cost
- [ ] 7.7 Render missing optional fields as em-dash (`—`)

## 8. Frontend — selection & keyboard shortcuts

- [ ] 8.1 Implement click-to-toggle selection
- [ ] 8.2 Implement shift-click range selection (A → B in rendered order)
- [ ] 8.3 Implement `a` shortcut: select all currently visible (rendered) items
- [ ] 8.4 Implement `esc` shortcut: clear selection
- [ ] 8.5 Implement `g` / `l` shortcuts: switch view
- [ ] 8.6 Implement `/` shortcut: focus the search input
- [ ] 8.7 Shortcut dispatcher MUST check `document.activeElement` and ignore shortcuts when focus is in `<input>` or `<textarea>`
- [ ] 8.8 Update URL query string on view switch and filter change so reload reproduces state

## 9. Frontend — bulk download

- [ ] 9.1 Implement per-image single download via `<a href="<hosted_url>" download>` (no ZIP)
- [ ] 9.2 Implement bulk "Download ZIP": fetch each selected hosted URL as `ArrayBuffer`, pass to `fflate.zip`, wrap as `Blob`, trigger download via hidden `<a>`
- [ ] 9.3 Accumulate total selected `file_size` from entry metadata; disable the button with tooltip when the sum exceeds the soft cap
- [ ] 9.4 ZIP filenames use each entry's `<slug>-<WxH>.webp` basename
- [ ] 9.5 Verify the flow works on current iOS Safari (manual test documented in deploy step)

## 10. Tests — backend

- [ ] 10.1 `tests/test_gallery_index.py` — scanner builds entries from seeded `/data/images` tree; missing sidecar skipped; missing `platform` tolerated; mtime-fallback when `created_at` absent
- [ ] 10.2 `tests/test_gallery_index.py` — filter cases: brand list, platform exact, date range, `q` substring case-insensitivity, dimension mins, all combinable
- [ ] 10.3 `tests/test_gallery_index.py` — sort cases: default `created_desc`, `created_asc`, `cost_desc`, `size_desc`
- [ ] 10.4 `tests/test_gallery_index.py` — pagination: offset/limit slicing; `limit=1000` clamped to 500
- [ ] 10.5 `tests/test_gallery_api.py` — Starlette TestClient: all endpoints return expected shapes on a seeded tree
- [ ] 10.6 `tests/test_gallery_api.py` — `POST /api/reindex` picks up a newly-written sidecar
- [ ] 10.7 `tests/test_gallery_api.py` — `TailnetOnlyMiddleware`: public Host → 404 on `/gallery/*`, public Host → 200 on `/mcp`, Tailnet Host → 200 on `/gallery/`

## 11. Tests — frontend smoke

- [ ] 11.1 Lightweight HTML/JS unit test (no browser) verifying the URL-query-string serializer round-trips filter state
- [ ] 11.2 Pure-JS test for the ZIP filename derivation from entry path

## 12. Docs

- [ ] 12.1 Add a "Gallery" subsection under "HTTP serving" in `CLAUDE.md` describing the sub-app mount, auth model, reindex cadence, and that it is Tailnet-only
- [ ] 12.2 Update README with a one-liner pointing at the internal Tailnet URL and noting the `/mcp` + public image routes are unchanged
- [ ] 12.3 Note the vendored `fflate` version + checksum in `gallery/static/README.md`

## 13. Deploy

- [ ] 13.1 Merge to `main`; let CI cut a release tag + image
- [ ] 13.2 Decide the internal Tailnet hostname (e.g. `bildsprache-gallery.<tailnet>.ts.net`) and update `compose.yaml` with the docktail label
- [ ] 13.3 Set `GALLERY_TAILNET_HOSTNAME=<hostname>` in the stack env
- [ ] 13.4 Recreate the stack; tail logs for the first reindex success line
- [ ] 13.5 Verify from a Tailnet-connected device: `GET /gallery/` → 200 HTML
- [ ] 13.6 Verify from the public hostname: `GET https://bildsprache.cdit-dev.de/gallery/` → 404 (must NOT be 200)
- [ ] 13.7 Verify `GET https://bildsprache.cdit-dev.de/mcp` → unchanged behavior
- [ ] 13.8 Verify `GET https://img.cdit-works.de/<brand>/<existing>.webp` → unchanged behavior
- [ ] 13.9 Manual UX pass on iOS Safari: browse, filter, select 3-5, download ZIP, confirm single `.zip` lands in Files
- [ ] 13.10 Manual UX pass on desktop: keyboard shortcuts, grid↔list toggle, URL-reload reproduces state
