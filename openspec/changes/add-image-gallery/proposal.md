## Why

Every `generate_image` call writes a WebP plus a JSON sidecar to `/data/images/<brand>/` and returns a hosted URL under `img.cdit-works.de`, but there is no way to browse what has accumulated. The only paths today are (a) remember the exact URL, or (b) SSH to the host and list the directory. That makes it painful to re-use good output, audit what was generated for a client, or casually review the archive. It also means good images effectively disappear the moment the chat scrolls past them.

A gallery solves this directly, and the data is already in the right shape — each sidecar holds prompt, model, cost, dimensions, brand, and file size — so the index is effectively free. The one friction point the user has flagged explicitly: downloads must work in bulk and must work on iOS (mobile Safari), so the multi-download mechanism has to be chosen with that constraint in mind.

## What Changes

- **Starlette gallery app mounted on the existing HTTP app.** In `server.py`, alongside the current `_mount_static_files(app)` call, mount a new Starlette sub-app at `/gallery` that serves HTML, JS, CSS, and a small JSON API. Single process, single port — no new service.
- **JSON sidecar index.** A startup scan builds an in-memory index from every `<brand>/<slug>-<WxH>.json` sidecar. The index is refreshed on-demand via an HTTP endpoint (`POST /gallery/api/reindex`) and on a lightweight background timer (configurable interval, default 5 minutes). No database — sidecars are the source of truth.
- **Gallery JSON API.**
  - `GET /gallery/api/images` — list with query params for filtering (`brand`, `platform`, `from`, `to`, `q` for prompt substring) and paging (`limit`, `offset`). Returns metadata from sidecars plus the hosted URL.
  - `GET /gallery/api/images/<path>` — single image details (for a deep link).
  - `POST /gallery/api/reindex` — trigger rescan.
- **Frontend (vanilla, single page).** Static HTML + JS + CSS served from the gallery app. No build step — shipped as-is in the image. Features:
  - Grid view (default) and list view (table with prompt, brand, date, dims, model, cost).
  - Filter bar: brand context, date range, platform/dimensions, prompt text search.
  - Multi-select (click to select, shift-click for range, "select all visible", "clear").
  - Per-image actions: open hosted URL, copy URL, copy prompt, download single.
  - Bulk action: download selected as a single `.zip` (client-side, see auth/download section).
- **Bulk download — client-side ZIP.** Use a small client-side ZIP library (e.g. `fflate`, bundled as a single ~20 KB file, no build step) to fetch each selected WebP in the browser and stream them into a single downloadable archive. Works on mobile Safari because the final download is a single Blob URL. No server-side zip endpoint; no tempfile lifecycle to manage.
- **Auth: Tailscale-only.** The gallery is not exposed publicly. The existing `img.cdit-works.de` static mount stays public (images are already published there). The gallery route is reachable only from the Tailnet — implemented either via (a) a separate Tailscale-only listener/port inside the container exposed via docktail, or (b) a request-level check on `/gallery/*` that verifies the inbound IP is on the Tailnet. The decision between (a) and (b) goes in design.md; default assumption: separate docktail-exposed service on an internal-only hostname.
- **Index metadata extensions.** No changes to sidecars themselves; the gallery only reads what's there. If fields are missing on older sidecars (e.g. early generations before `platform` was tracked), the gallery degrades gracefully (shows dims, hides missing fields).
- **Thumbnail strategy.** Initial version serves the full WebP as the grid thumbnail via the browser's native `loading="lazy"` + `decoding="async"` — WebPs are already small (~50-200 KB) and the static mount handles range requests. A separate thumbnail pipeline is **explicitly out of scope** for v1 and noted as a follow-up if perf suffers.

## Capabilities

### New Capabilities

- `image-gallery`: an authenticated (Tailnet-only) web UI and supporting JSON API for browsing, filtering, and bulk-downloading every image the server has generated, sourced from the on-disk WebP + JSON sidecar layout.

### Modified Capabilities

_None — no pre-existing specs in `openspec/specs/`._

## Impact

- **Code**
  - `mcp_bildsprache/server.py` — mount Starlette gallery sub-app alongside the existing static mount; wire the reindex timer into the app lifespan.
  - `mcp_bildsprache/gallery/` — new package:
    - `gallery/app.py` — Starlette routes (HTML + JSON API).
    - `gallery/index.py` — sidecar scanner, filter/search logic, reindex scheduler.
    - `gallery/static/` — HTML, JS, CSS, `fflate.min.js`.
  - `mcp_bildsprache/config.py` — new settings: gallery enable flag, reindex interval, Tailnet CIDR list (if using IP-check mode), data dir is already there.
  - `mcp_bildsprache/storage.py` — no changes; sidecars already contain everything the gallery needs.
- **Deployment**
  - `compose.yaml` — if going with the separate-listener approach, add the Tailscale-only port/docktail label; if using IP check, no compose change needed.
  - Production URL — internal-only hostname on the Tailnet (e.g. `bildsprache-gallery.<tailnet>.ts.net`). Design doc picks the exact form.
- **Performance** — at the current generation rate, the sidecar index is small (hundreds to low thousands of entries) and fits easily in memory. The reindex timer is cheap (stat + json parse per file). If count grows substantially (>10k), a follow-up change can move to a SQLite index; not required for v1.
- **Tests** — new `tests/test_gallery_index.py` (scanner + filter/search), `tests/test_gallery_api.py` (Starlette endpoints with a seeded `/data/images` tree), and a lightweight browser-free test of the static frontend's URL assembly. No JS framework tests — the frontend is small and mostly serves data.
- **Docs** — new "Gallery" section in `CLAUDE.md` under "HTTP serving"; README gets a one-liner pointing at the internal gallery URL.
- **Out of scope** — editing / re-generating from the gallery, deleting images, tagging/favorites, user accounts beyond Tailnet membership, server-side thumbnail generation, uploads from outside the generator, sharing links to non-Tailnet users.
