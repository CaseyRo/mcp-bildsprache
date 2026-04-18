## ADDED Requirements

### Requirement: Gallery is mounted as a sub-app on the existing HTTP app

The gallery SHALL be served by a Starlette sub-app mounted at the path prefix `/gallery` on the same ASGI application that hosts `/mcp` and the static `/data/images` mount. No additional container, port, or process SHALL be introduced.

#### Scenario: Gallery, MCP, and static mount coexist

- **WHEN** the server is started in HTTP mode
- **THEN** `GET /gallery/` MUST return the gallery HTML shell
- **AND** `POST /mcp` MUST continue to route to the MCP handler
- **AND** `GET /<brand>/<slug>-<WxH>.webp` MUST continue to serve the file from `/data/images`

### Requirement: Gallery access restricted to the Tailnet

The `/gallery/*` path prefix MUST be reachable only via the Tailnet-only hostname (docktail-exposed). The public Cloudflare-tunnel hostname MUST reject `/gallery/*` requests at the application layer in addition to any infrastructure-level restriction.

#### Scenario: Public hostname rejects gallery requests

- **WHEN** an HTTP request is made to `https://bildsprache.cdit-dev.de/gallery/` with the public `Host` header
- **THEN** the server MUST respond with HTTP 400 or 404
- **AND** the response MUST NOT include HTML gallery content

#### Scenario: Tailnet hostname serves gallery

- **WHEN** an HTTP request is made with a `Host` header matching the configured Tailnet hostname
- **THEN** `GET /gallery/` MUST return HTTP 200 with the gallery HTML shell

#### Scenario: Public hostnames for MCP and images unaffected

- **WHEN** `/mcp` or `/<brand>/*.webp` is requested via the public hostname
- **THEN** the Host-header middleware MUST NOT block the request

### Requirement: Index is built from JSON sidecars

On app startup the gallery SHALL walk `/data/images/**/*.json`, parse each sidecar, and build an in-memory list of gallery entries. Each entry SHALL expose: relative path, hosted URL, brand, prompt, lowered prompt (for search), model, cost estimate, width, height, platform (optional), file size, and `created_at` timestamp (from sidecar if present, else file mtime).

#### Scenario: Entry constructed from sidecar

- **WHEN** a valid sidecar file exists alongside a WebP
- **THEN** exactly one entry MUST be added to the index
- **AND** `hosted_url` MUST match the public `img.cdit-works.de` URL for that WebP
- **AND** `brand` MUST be the top-level directory under `/data/images/`

#### Scenario: Missing optional fields tolerated

- **WHEN** a sidecar lacks a `platform` field
- **THEN** the entry MUST still be indexed with `platform=None`
- **AND** subsequent filtering by `platform` MUST NOT exclude entries on equality when the filter is unset

#### Scenario: WebP without a sidecar is skipped

- **WHEN** a WebP file exists without a matching `.json` sidecar
- **THEN** it MUST NOT appear in the index
- **AND** no error MUST be raised

### Requirement: Index is refreshed on startup, on a timer, and on demand

The index SHALL be (a) built synchronously before the first request is served, (b) rebuilt by a background task on a configurable interval (default 5 minutes), and (c) rebuilt immediately when `POST /gallery/api/reindex` is called. No filesystem watcher SHALL be used.

#### Scenario: Startup scan completes before first request

- **WHEN** the server receives any `/gallery/api/*` request
- **THEN** the response MUST reflect at least one completed index scan

#### Scenario: Background reindex refreshes new entries

- **WHEN** a new WebP + sidecar pair is written to `/data/images/` after startup
- **THEN** within one reindex interval it MUST appear in subsequent `/gallery/api/images` responses
- **AND** without any restart or external signal

#### Scenario: POST /gallery/api/reindex triggers immediate rebuild

- **WHEN** `POST /gallery/api/reindex` is received
- **THEN** the server MUST rebuild the index synchronously before responding
- **AND** the response MUST include the new total entry count

### Requirement: List endpoint supports filtering, sorting, and pagination

`GET /gallery/api/images` SHALL accept the following query parameters and return a filtered, sorted, paged list.

Filter params: `brand` (comma-separated or repeatable), `platform` (exact match), `from` / `to` (ISO-8601 on `created_at`), `q` (case-insensitive substring over `prompt_lower`), `min_width`, `min_height`.

Sort: `sort` ∈ {`created_desc`, `created_asc`, `cost_desc`, `size_desc`}, default `created_desc`.

Paging: `limit` (default 100, max 500), `offset` (default 0).

#### Scenario: Default response

- **WHEN** `GET /gallery/api/images` is called with no query parameters
- **THEN** response MUST include `total`, `limit=100`, `offset=0`, and `items` sorted by `created_at` descending
- **AND** `items` length MUST be `min(100, total)`

#### Scenario: Prompt substring search is case-insensitive

- **WHEN** `GET /gallery/api/images?q=forest` is called
- **THEN** the result MUST contain every indexed entry whose lowered prompt contains `"forest"` as a substring
- **AND** MUST NOT contain entries without such a substring

#### Scenario: Date-range filter

- **WHEN** `GET /gallery/api/images?from=2026-01-01&to=2026-01-31` is called
- **THEN** every item in the response MUST have `created_at` within that inclusive range

#### Scenario: Brand filter is inclusive

- **WHEN** `GET /gallery/api/images?brand=casey-berlin,cdit` is called
- **THEN** every item MUST have `brand` equal to `casey-berlin` or `cdit`
- **AND** no other brand MUST appear

#### Scenario: Pagination

- **WHEN** `GET /gallery/api/images?limit=10&offset=20` is called
- **THEN** the response `items` MUST contain the entries at positions 21-30 of the full sorted result
- **AND** `total` MUST reflect the full filtered count (not the slice size)

#### Scenario: Limit is bounded

- **WHEN** `GET /gallery/api/images?limit=1000` is called
- **THEN** the returned `items` length MUST NOT exceed 500

### Requirement: Single-entry endpoint supports deep links

`GET /gallery/api/images/{path}` SHALL return the full `GalleryEntry` for a single image, where `{path}` is URL-encoded and matches the entry's `path` field.

#### Scenario: Known path

- **WHEN** `GET /gallery/api/images/casey-berlin/morning-walk-1600x900.webp` is called and that entry exists
- **THEN** the response MUST be the entry object with HTTP 200

#### Scenario: Unknown path

- **WHEN** the requested path does not exist in the index
- **THEN** the response MUST be HTTP 404

### Requirement: Frontend is served as static assets with no build step

The gallery frontend SHALL consist of one HTML file, one JS module, one CSS file, and a vendored `fflate.min.js`, served from `/gallery/static/`. No transpilation, bundling, or npm toolchain SHALL be part of the build.

#### Scenario: Static assets served

- **WHEN** `GET /gallery/static/fflate.min.js` is called from the Tailnet hostname
- **THEN** the response MUST be HTTP 200 with `Content-Type: application/javascript`

### Requirement: Frontend supports grid and list views

The frontend SHALL render the indexed images in either a grid view (default) or a list view. The active view SHALL be reflected in the URL query string so links are shareable.

#### Scenario: Grid view is default

- **WHEN** `GET /gallery/` is loaded fresh with no query string
- **THEN** the rendered view MUST be the grid

#### Scenario: Switch to list view

- **WHEN** the user activates the list view (click or `l` key)
- **THEN** the DOM MUST render a table with columns for prompt, brand, date, dimensions, model, cost
- **AND** the URL MUST be updated to include `view=list`

### Requirement: Frontend supports filtering via URL-synced state

The frontend SHALL expose filter controls for brand, platform, date range, and prompt text search. Filter state SHALL be serialized into the URL query string such that reloading the URL reproduces the filtered view.

#### Scenario: Filter state in URL

- **WHEN** the user sets brand filter `casey-berlin` and prompt search `forest`
- **THEN** the URL MUST reflect `brand=casey-berlin&q=forest`
- **AND** reloading the URL MUST restore the same filtered view

#### Scenario: Prompt search is debounced

- **WHEN** the user types continuously into the search field
- **THEN** the frontend MUST NOT issue a backend request on every keystroke
- **AND** the debounce interval MUST be at most 250 ms

### Requirement: Frontend supports multi-select and clear

The frontend SHALL support selecting multiple images via click (toggle one), shift-click (range), `a` key (select all currently visible), and `esc` key (clear selection).

#### Scenario: Shift-click range select

- **WHEN** the user clicks item A, then shift-clicks item B (with B later in the rendered order)
- **THEN** all items from A through B inclusive MUST be marked selected

#### Scenario: Select-all-visible affects only rendered items

- **WHEN** the user presses `a` while 40 items are rendered (out of a filtered total of 200)
- **THEN** exactly those 40 items MUST be selected
- **AND** the remaining 160 MUST NOT be selected

#### Scenario: Escape clears selection

- **WHEN** any items are selected and the user presses `esc`
- **THEN** the selection MUST become empty
- **AND** the selection-count indicator MUST update accordingly

### Requirement: Bulk download produces a single ZIP in the browser

When the user triggers "Download ZIP" with at least one selected image, the frontend SHALL fetch each selected WebP, assemble a single ZIP archive in memory using `fflate`, and trigger a browser download of a single Blob. The flow MUST work on current iOS Safari.

#### Scenario: Single-image download path still supported

- **WHEN** the user triggers a single-image download from the per-image menu
- **THEN** the download MUST use the hosted WebP URL directly (no ZIP wrapping)

#### Scenario: Multi-image ZIP

- **WHEN** the user has 5 images selected and triggers "Download ZIP"
- **THEN** the browser MUST download exactly one `.zip` file
- **AND** the archive MUST contain all 5 WebPs using their `<slug>-<WxH>.webp` filenames

#### Scenario: Soft cap prevents oversized archives

- **WHEN** the selected total file size exceeds the configured soft cap (default 250 MB)
- **THEN** the "Download ZIP" action MUST be disabled
- **AND** the UI MUST display a tooltip explaining the limit and suggesting a smaller batch

### Requirement: Keyboard shortcuts

The frontend SHALL implement the following keyboard shortcuts when focus is not in a text input: `g` switches to grid view, `l` switches to list view, `a` selects all visible items, `esc` clears selection, `/` focuses the search input.

#### Scenario: Shortcut ignored while typing in search

- **WHEN** the search input has focus and the user types `g`, `l`, `a`, or `/`
- **THEN** the character MUST be inserted into the input
- **AND** the view MUST NOT switch and the selection MUST NOT change

#### Scenario: Each shortcut binds to its documented action

- **WHEN** the user presses `g` with no input focused
- **THEN** the view MUST switch to grid
- **WHEN** the user presses `l` with no input focused
- **THEN** the view MUST switch to list

### Requirement: Default sort is newest first

The default sort order for list responses and the default rendering order in both views SHALL be `created_desc`.

#### Scenario: Default sort returns newest first

- **WHEN** `GET /gallery/api/images` is called with no `sort` parameter
- **THEN** consecutive items in `items` MUST satisfy `items[i].created_at >= items[i+1].created_at`

### Requirement: Missing sidecar fields render as em-dashes in the UI

Where a sidecar lacks an optional field (e.g. `platform`), the UI SHALL render the missing value as an em-dash (`—`) rather than an empty cell or `null`.

#### Scenario: Missing platform rendered as em-dash

- **WHEN** an entry with `platform=None` is rendered in list view
- **THEN** the platform cell MUST contain an em-dash character
