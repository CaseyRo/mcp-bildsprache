## Context

Every successful `generate_image` call writes two artifacts into `/data/images/<brand>/`:

- `<slug>-<WxH>.webp` — the final processed image served publicly at `https://img.cdit-works.de/<brand>/<slug>-<WxH>.webp`.
- `<slug>-<WxH>.json` — a sidecar carrying prompt, `prompt_hash`, model, `cost_estimate`, dimensions, `file_size`, and brand context.

The sidecar format was designed for provenance but incidentally makes it a perfectly adequate index: every field a browsing UI would want is already on disk, keyed by a filename that maps 1:1 to the hosted URL. After months of use there is no way to look back at what has been generated other than knowing a URL or SSH-ing to the host and `ls`-ing the directory tree.

Constraints shaping the design:

- **HTTP surface already exists.** `server.py` runs `mcp.http_app(transport="http")` and mounts `/data/images` statically at `/`. The MCP protocol owns `/mcp`. A gallery has to coexist with both.
- **Single-user deployment.** No RBAC, no accounts, no audit trail needed beyond what sidecars already provide.
- **Mobile matters.** The user explicitly called out iOS Safari for bulk downloads. This rules out anything that depends on desktop-only file APIs (e.g. `File System Access API`) and pushes toward "produce one Blob, download it" patterns.
- **Tailnet, not internet.** The generated images themselves are already public at `img.cdit-works.de`. The gallery — which lists *all* prompts, costs, and images together — should not be. Tailscale is the existing private network; docktail is already used across CDIT services.
- **No build step.** The repo is pure Python with `uv`. Adding a Node toolchain for the frontend is disproportionate for a single-page admin UI.

## Goals / Non-Goals

**Goals:**

- Browse every image ever generated, filter by brand / platform / date / prompt text, in grid or list view.
- Multi-select + single-action bulk download that works on iOS Safari.
- Zero new persistent infrastructure: index built from existing sidecars, no database.
- Private by default: gallery reachable only from the Tailnet, images-only static mount stays where it is.
- Deploys with the existing Docker compose stack on the same container, same port, same release flow.
- Frontend ships as static assets in the image — no build step, no transpile, no npm.

**Non-Goals:**

- Editing, deleting, or regenerating images from the gallery.
- Tags, collections, favorites, user-provided metadata.
- Server-side thumbnail generation (WebPs are already small).
- Public sharing / link sharing outside the Tailnet.
- Uploads from outside the generator (the gallery is read-only over the image corpus).
- Any form of account system, login, or per-user state.

## Decisions

### 1. Mount as a Starlette sub-app on the existing HTTP app

Rather than a separate service, the gallery is a `mcp_bildsprache.gallery` package with its own Starlette `Router`, mounted at `/gallery` on the same ASGI app that hosts `/mcp` and the static `/` mount. FastMCP's `http_app()` already returns a mutable Starlette app.

**Why:** one container, one port, one TLS cert, one release pipeline. The data the gallery needs (`/data/images`, sidecars) is already mounted in the same process. A second service would duplicate all of this for negative benefit.

**Alternatives considered:**

- *Separate container / sidecar*: rejected for the reasons above.
- *Pure static files, no API*: possible (write a pre-generated `index.json` on every `generate_image` call), but tightly couples the write path to the gallery and means the index goes stale if files arrive another way (e.g. a manual copy during recovery). The API path is more robust.

### 2. Starlette, not FastAPI

The FastMCP HTTP stack is already Starlette under the hood. There is no validation-heavy API surface here (three endpoints, small payloads); Pydantic models for request/response are overkill.

**Why:** keep the dependency footprint flat. FastAPI would pull nothing new that isn't already transitively present, but it would invite overengineering.

### 3. In-memory index built by scanning sidecars

On app startup, walk `/data/images/**/*.json`, parse each sidecar, and build:

```python
@dataclass
class GalleryEntry:
    path: Path            # relative to /data/images
    hosted_url: str
    brand: str            # derived from top-level dir
    prompt: str
    prompt_lower: str     # pre-lowered for fast substring search
    model: str
    cost_estimate: str
    width: int
    height: int
    platform: str | None  # may be absent on older sidecars
    file_size: int
    created_at: datetime  # from sidecar if present, else mtime
```

Index stored as `list[GalleryEntry]` + a `dict[str, GalleryEntry]` by path for deep-link lookups. Refresh triggered (a) at startup, (b) by a `POST /gallery/api/reindex`, (c) on a background interval (default 5 minutes, configurable). No file watcher.

**Why:** the total dataset is small (hundreds to low thousands of entries; each sidecar is a few hundred bytes). Scanning all of them takes milliseconds on modern disks. A SQLite or similar persistent index is real engineering for an imagined scale problem — the 5-minute background refresh plus manual reindex covers everything the user actually needs.

**Alternatives considered:**

- *SQLite-backed index*: adds a migration surface, stat-vs-row drift questions, and persistence we don't need. Deferred: if entry count exceeds ~10k, revisit.
- *File watcher (inotify/FSEvents)*: cross-platform complexity for a cached rescan that already takes milliseconds. Not worth it.
- *Generate index on every write in `storage.py`*: couples write path to gallery; breaks if images arrive any other way.

### 4. Three-endpoint JSON API

```
GET  /gallery/                  → HTML shell (single-page app)
GET  /gallery/static/<file>     → static JS/CSS/wasm
GET  /gallery/api/images        → filtered, paged list
GET  /gallery/api/images/{path} → single entry (deep links)
POST /gallery/api/reindex       → force rescan
```

`GET /gallery/api/images` query params:

- `brand` (repeatable or comma-separated) — filter by brand context.
- `platform` — exact match on `platform` field.
- `from` / `to` — ISO-8601 dates on `created_at`.
- `q` — case-insensitive substring over `prompt_lower`.
- `min_width`, `min_height` — optional dimension filters.
- `limit` (default 100, max 500), `offset`.
- `sort` — `created_desc` (default), `created_asc`, `cost_desc`, `size_desc`.

Response:

```json
{
  "total": 1234,
  "limit": 100,
  "offset": 0,
  "items": [ { GalleryEntry, ... } ]
}
```

**Why:** server-side filtering keeps the payload small on mobile; everything the client does (render, zip) can scale with what's returned, not with the whole corpus.

### 5. Auth: Tailscale-only via a docktail-exposed internal listener

The compose file already supports `docktail` labels for exposing services selectively to the Tailnet. The gallery is bound to an internal hostname (e.g. `bildsprache-gallery.<tailnet>.ts.net`) and is not reachable from the public Cloudflare tunnel.

**Why:** defense in depth. Relying only on an IP allowlist on `/gallery/*` would keep one codepath from leaking, but docktail already handles the whole-service case with one label. It's the same pattern every other internal CDIT service uses. Cognitive overhead: none.

**Alternatives considered:**

- *IP allowlist in application code*: a request-level check that the caller's IP is in a Tailnet CIDR. Works, but reinvents what docktail + Tailscale already do, and it's one bug away from leaking. Only a fallback if docktail can't be used for some reason.
- *Reuse the existing MCP auth (Keycloak + bearer)*: wrong tool. The gallery is a browser UI for one user, not a programmatic MCP client. Signing into Keycloak to browse thumbnails is the wrong UX.

Implementation shape: the gallery's routes live on the same Starlette app but the app is exposed over two hostnames — the public `bildsprache.cdit-dev.de` (Cloudflare tunnel; only `/mcp` and `/<brand>/*` reachable) and the internal `bildsprache-gallery.*.ts.net` (docktail; all paths reachable). An app-level middleware on `/gallery/*` refuses the request when the inbound `Host` header isn't the Tailnet hostname — this is a cheap, auditable second line of defense against misconfiguration.

### 6. Bulk download: client-side ZIP with `fflate`

On multi-select + "Download ZIP", the frontend:

1. For each selected entry, `fetch()` the hosted WebP URL directly (same-origin from the Tailnet gallery's perspective, CORS from the public mount — so use the *internal* URL, which maps through the same listener).
2. Collect `ArrayBuffer`s in memory.
3. Run `fflate.zip()` to produce a single archive as a `Uint8Array`.
4. Wrap in a `Blob`, create an object URL, trigger download via a hidden `<a download>`.

`fflate` is ~20 KB minified, zero dependencies, works in a Web Worker if we want to keep the main thread free. Vendored as `gallery/static/fflate.min.js`.

**Why this works on iOS Safari:** the final step is a single click on an `<a href="blob:..." download>` — Safari's canonical "download this file" path. No `showSaveFilePicker`, no streaming APIs, no SharedArrayBuffer requirement.

**Alternatives considered:**

- *Server-side zip endpoint*: simple (`zipfly` or stream `zipfile`), but introduces a tempfile / streaming lifecycle to manage on the server, and a DOS surface (someone selects 5000 images). Client-side keeps server boring.
- *Download each file one at a time*: rejected — iOS blocks rapid-fire download links.
- *JSZip*: larger than fflate (~95 KB vs 20 KB), slower. fflate is a strict improvement for this use case.

**Memory cap:** the client enforces a soft limit (e.g. 250 MB of accumulated selection). Above that, the button disables with a tooltip ("Too many for one archive — download in batches"). Cap is a config constant, tunable without ceremony.

### 7. Thumbnails: none, first pass

The grid renders the hosted WebP at the display size with `loading="lazy"` and `decoding="async"`. WebPs for @cdit posts land around 50-200 KB; a grid of 50 lazy-loaded items is well under 10 MB if they all scroll into view. The image server already supports range requests.

**Why:** a separate thumbnail pipeline (resize on first read, cache to `/data/images/.thumbs/`) is a meaningful amount of code and a second lifecycle to manage. It's the right move *if* perf suffers — measure first.

**Fallback plan** (out of scope for this change, documented here so we don't forget): a `GET /gallery/api/thumb/<path>?w=320` endpoint that lazy-generates and caches a `<slug>-<WxH>-thumb320.webp` beside the original. Trivial to add if needed.

### 8. Frontend: vanilla JS, no framework

Single HTML file + one JS module + one CSS file. State held in a small `GalleryState` object, DOM rendered with template literals + `element.replaceChildren(...)`. No React, no Vue, no Svelte, no build step.

**Why:** the UI has ~5 interactive regions (filter bar, view toggle, grid/list, selection, download). A framework would be 50 KB of runtime to manage what vanilla JS manages in ~400 lines. Easier to deploy (just static files), easier to audit, and nobody else will ever work on it.

**Rules of thumb for the vanilla path:**

- Use native `<details>`, `<dialog>`, `<input type="date">`, etc. before reinventing.
- Filter state serialized into the URL query string so deep links and back/forward work.
- Grid / list / select / clear keyboard-driven where sensible (`g` = grid, `l` = list, `a` = select all visible, `esc` = clear selection).
- One CSS file, custom properties for theme tokens (match the CDIT-ish look if easy; don't bikeshed visual polish beyond "clean and calm").

### 9. Lifespan integration

Starlette lifespan startup:

1. First index scan (blocking — need it before first request anyway).
2. Spawn the periodic reindex task (`asyncio.create_task(_reindex_loop(...))`).

Lifespan shutdown cancels the background task. All tied into FastMCP's `http_app()` lifespan via Starlette composition.

## Risks / Trade-offs

- **[Public static mount still serves the images themselves]** → The gallery is private, but `img.cdit-works.de/<brand>/<slug>.webp` remains public by design. Mitigation: accepted — this is the existing contract, and the gallery's privacy value is in the *listing* (prompts, costs, totals), not in per-image secrecy. Revisit only if a use case needs per-brand private corpora.
- **[In-memory index grows unbounded over years]** → At current pace probably fine for a long time; at some point, the full scan on every reindex becomes slow. Mitigation: incremental reindex (stat-based diff against last scan) as a follow-up. Not needed for v1.
- **[Client-side zip blows up RAM on huge selections]** → Soft 250 MB cap with a clear disabled-state tooltip. Users can always fall back to selecting fewer images and running the download twice.
- **[Sidecars may be incomplete on older entries]** → Before some fields existed (e.g. `platform`), sidecars don't have them. Index fields are `Optional`, and the UI renders em-dashes for missing values. Not a bug, a visible gap.
- **[Docktail label misconfig exposes gallery publicly]** → The Host-header middleware on `/gallery/*` is the second gate. Additionally, the migration plan includes a post-deploy check (`curl https://bildsprache.cdit-dev.de/gallery/` must 404 or 400, not 200) before declaring done.
- **[Race between reindex and `generate_image` writing new files]** → The scanner reads JSONs atomically (open → read → close). Partial writes are already avoided upstream by writing to a temp file and renaming. Worst case: a sidecar appears mid-scan and is missed this tick; picked up next tick. Acceptable.
- **[Filter by prompt text on mobile keyboards]** → Free-text search against lowered prompt strings is fine for a few thousand entries but can feel laggy if typed quickly. Debounce 200 ms on input; run the filter in a `requestIdleCallback`.
- **[Adding assets to the image increases layer size]** → ~30 KB of static frontend + `fflate`. Negligible in a container already shipping Pillow, pydantic, httpx, FastMCP.

## Migration Plan

1. Implement the `gallery/` package + frontend on a feature branch; add tests for the scanner, filters, and the Host-header middleware.
2. Merge to `main`; CI releases a new version and a new image.
3. On the production host, update `compose.yaml`: add the docktail label exposing the new internal hostname; recreate the stack.
4. Verify:
   - `curl -H 'Host: bildsprache-gallery.<tailnet>.ts.net' https://<internal>/gallery/` → 200 (HTML shell).
   - `curl https://bildsprache.cdit-dev.de/gallery/` → 400 (rejected by middleware).
   - `curl https://bildsprache.cdit-dev.de/mcp` → still works.
   - `curl https://img.cdit-works.de/<brand>/<existing>.webp` → still works.
5. Open the gallery in browser (desktop + iOS Safari), verify grid, list, filters, select, ZIP download.
6. Rollback: revert to prior image tag; remove the docktail label. No schema migrations, no destructive changes.

## Open Questions

- *Default sort order for empty search* — created-descending makes intuitive sense; confirm on first use.
- *Soft memory cap value* — 250 MB is an initial guess; may need adjustment after real usage.
- *Should we expose the prompt SHA-256 hash (already in EXIF) in the list view?* Deferred — no one has asked for it; adds visual noise.
- *Keyboard shortcut set* — the proposed `g / l / a / esc` is a v1 guess. Let real usage drive any additions.
- *Do we also want a "copy prompt" bulk action (to paste into a new generation)?* Out of scope for v1 but trivial to add later on top of the same selection model.
