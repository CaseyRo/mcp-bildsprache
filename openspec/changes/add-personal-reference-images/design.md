## Context

`mcp-bildsprache` currently turns a text prompt + brand context into a generated image through a fixed pipeline: `route_model` → `get_preset` → provider call → post-process → store. Everything between "prompt" and "bytes" is text-only. The provider modules (`gemini.py`, `bfl.py`, `recraft.py`) accept `(prompt, width, height)` and return `ProviderResult` — no image inputs anywhere.

That text-only assumption was fine when every brand's visual DNA could be expressed as a descriptive preset string. It breaks for `@casey.berlin`, because a personal brand requires identity fidelity, and no amount of text ("man in his late thirties, short brown hair…") gets a diffusion model to reliably render *Casey* or his two Stabyhoun dogs (Fimme, Sien).

The three providers diverge sharply in reference-image support:

| Provider | Reference support | Endpoint / shape |
|---|---|---|
| Gemini | Native multimodal | Append extra `inlineData` parts to `contents[0].parts` on the same `:generateContent` endpoint. |
| FLUX (BFL) | Separate endpoints | `flux-kontext-pro` accepts `input_image` (base64); `flux-2-pro` accepts `image_prompt`. `flux-2-max` (current default) is text-only. |
| Recraft V4 | Not on text-to-image endpoint | Current integration hits only the generations endpoint; no usable reference-image path without switching endpoints. |

Current relevant defaults and surfaces:

- `route_model` default → FLUX (`flux-2-max`), which is text-only.
- `FLUX_MODELS` dict already lists `flux-kontext-pro` but it is never selected by the current fallback chain `flux-2-max → flux-2-pro → flux-pro-1.1`.
- `/data/` is already the conventional mount root inside the container (`/data/images`, `FASTMCP_HOME=/data/fastmcp`); a new `/data/identity` volume fits the pattern.
- Stakeholder is a single user (Casey). No multi-tenant considerations. All `@casey.berlin` traffic is his own.

## Goals / Non-Goals

**Goals:**

- Every `generate_image` call with `context="@casey.berlin"` automatically gains identity fidelity when a person plausibly appears in the scene, without the caller doing anything special.
- The two-dog addition (Fimme + Sien) kicks in only for personal/reflective/outdoor contexts — never for work or client scenes — with a deterministic, inspectable heuristic.
- The compositional rule ("never face-to-camera, always doing something, never sole focal point in group shots") is enforced by prompt augmentation so it survives provider swaps.
- Reference-image plumbing is general-purpose: other brand contexts can adopt it later by dropping a manifest on the same volume.
- Identity assets stay private: never in the git repo, never in the public `img.cdit-works.de` static mount.
- Provider fallback remains robust: if the preferred reference-capable provider fails, the server falls through to *another* reference-capable provider before degrading to text-only.

**Non-Goals:**

- Identity packs for brands other than `@casey.berlin` in this change (the infra supports it, but populating other manifests is out of scope).
- Server-side face recognition, face-detection validation of output, or any ML layer beyond what the providers themselves do.
- UI for managing the manifest or previewing references — the volume + a README is the admin surface in v1.
- Style-reference images that aren't about identity (general mood-board references); only identity packs are modeled here.
- Re-generation-from-reference flows (e.g. "take this existing image and restyle it"). Only adding references to a fresh generation is in scope.
- Caching reference-image bytes in Redis or similar; the volume is local and fast enough.

## Decisions

### 1. Reference images delivered via a Docker volume, not in the repo

Identity photos live on a new named volume `identity-data`, mounted at `/data/identity/<brand>/`. The repo ships only a README + example manifest schema under `docs/identity/`.

**Why:** (a) personal likeness should not be committed to a public GitHub repo; (b) replacing a reference shot shouldn't require a commit + CI release + redeploy — the whole point of having CI own versioning is to reserve `main` for code changes, not asset churn; (c) matches the existing `/data/*` volume pattern (`images-data`, `fastmcp-data`).

**Alternatives considered:**

- *Commit to the repo*: rejected for privacy and the redeploy tax above.
- *Object storage (S3/R2)*: overkill for a single-user deployment; introduces a credentials dependency and a network hop on every generation call.
- *EXIF-based: pull identity refs from an existing image on disk*: tempting (sidecars already exist), but conflates "generated output" with "identity input" and makes it too easy to feed a bad generation back into the next one.

### 2. Manifest shape: slots + tag-based auto-resolution

`/data/identity/<brand>/manifest.json`:

```json
{
  "version": 1,
  "slots": {
    "casey":  { "files": ["casey-1.webp", "casey-2.webp"], "tags": ["person", "primary"] },
    "fimme":  { "files": ["fimme-1.webp"],                  "tags": ["dog", "companion"] },
    "sien":   { "files": ["sien-1.webp"],                   "tags": ["dog", "companion"] }
  },
  "rules": {
    "always_include": ["casey"],
    "include_if_prompt_matches": {
      "fimme": ["walk", "outside", "nature", "forest", "park", "reflection", "reflective", "personal", "home", "morning", "evening"],
      "sien":  ["walk", "outside", "nature", "forest", "park", "reflection", "reflective", "personal", "home", "morning", "evening"]
    },
    "exclude_if_prompt_matches": {
      "fimme": ["client", "office", "meeting", "keynote", "corporate", "business", "stage"],
      "sien":  ["client", "office", "meeting", "keynote", "corporate", "business", "stage"]
    }
  }
}
```

Resolution is a pure function: `resolve_identity(manifest, prompt) -> list[Path]`. Include-matchers are OR'd, exclude-matchers win over includes. `always_include` slots are only attached if the prompt indicates a person could appear (heuristic: absence of purely-object/landscape markers like "flat illustration, no people", "icon", "logo", "abstract pattern", "architectural detail"). When in doubt, include — the alternative (missing identity) is the failure mode we're fixing.

**Why this shape:** inspectable (readable JSON, no code to deploy), deterministic (same prompt + same manifest → same refs), and cheap to extend. No NLP, no LLM call in the path — just substring matching.

**Alternatives considered:**

- *Hand-written Python rules per brand*: rejected; turns every tweak into a code change.
- *Ask an LLM to pick references*: rejected for v1; adds latency, cost, nondeterminism, and — for a single-user system — pays for flexibility no one needs.
- *Tag the slots instead of the prompt*: tags are already on slots for debugging / future use; the primary match is on `prompt` because that's the signal we have.

### 3. Provider plumbing: all providers accept `reference_images`, but behave differently

New signature on every provider module:

```python
async def generate_<provider>(
    prompt: str,
    width: int = ...,
    height: int = ...,
    reference_images: list[bytes] | None = None,
    ...,
) -> ProviderResult
```

- **Gemini** — append each image as an extra `inlineData` part in `contents[0].parts`. Mime inferred from bytes (Pillow probe) or passed alongside.
- **FLUX (BFL)** — when `reference_images` is non-empty, route to `flux-kontext-pro` with `input_image` (first reference — kontext-pro is single-input). When multiple refs are supplied and only a single-input model is available, concatenate them into one collage image (Pillow grid, 1×N) as a pragmatic workaround. `flux-2-pro` path uses `image_prompt` (also single).
- **Recraft V4** — receives `reference_images`, immediately logs a one-line `info` noting they were dropped, and proceeds with the text-only request. The prompt-engineering layer upstream (`presets.py`) is responsible for compensating with descriptive text when Recraft is selected.

**Why uniform signatures:** keeps the `server.py` call-site trivial (`PROVIDERS[key](..., reference_images=refs)`) and makes it obvious which providers support what by reading the one function.

**Alternatives considered:**

- *Different signatures per provider*: rejected; forces `server.py` to know which providers accept references and creates a compound-dispatch mess at the call site.
- *Single server-level "if Recraft, strip refs" check*: acceptable but less honest than having the provider log the drop itself — the log is the audit trail when someone asks "why didn't my reference image do anything?"

### 4. Routing: identity-bearing requests prefer reference-capable providers

`route_model` gains a new input: `has_references: bool`. When true:

1. If the auto-routed choice is Recraft, switch to FLUX (unless model_hint explicitly pins Recraft).
2. FLUX fallback chain becomes `flux-kontext-pro → flux-2-pro → gemini`. Dropping `flux-2-max` from the chain when refs are present is important — we'd silently lose the identity signal otherwise.
3. Cross-provider `FALLBACKS` map gets an identity-aware variant: BFL → Gemini (both reference-capable), rather than BFL → anywhere.

Non-reference-bearing requests keep today's routing untouched.

**Why:** falling back to a text-only model when references were supplied is a silent correctness bug. Better to fail loudly than produce a "fine-looking but not me" image the user only notices much later.

### 5. Compositional rule is prompt-level, not model-level

The face-not-to-camera / doing-something / not-sole-focal-point clause is appended to the `@casey.berlin` preset string at prompt-assembly time:

> Composition: when a person appears, they are embedded in the scene doing something, never face-to-camera, never centered as the sole focal point. If multiple people are present, the subject is one of them — not the lead.

**Why:** portable across providers (works on a provider we haven't integrated yet), visible in the final prompt for debugging, and defeatable if someone really wants a straight portrait by clearing the preset. No custom provider parameters to maintain.

### 6. Indexing: startup + on-demand, no filesystem watcher

The manifest is loaded at server startup and cached in process memory. A `POST /gallery/api/reindex` endpoint is *not* added in this change; for v1, updating the manifest requires a container restart. Restart is cheap, stdio-mode unaffected, and avoids polluting the HTTP surface with admin endpoints.

**Alternative considered:** filesystem watcher. Rejected — added complexity for a workflow that will change rarely in practice.

### 7. `include_dogs` override

`generate_image` gains an optional `include_dogs: bool | None = None` parameter.

- `None` (default): use the manifest heuristic.
- `True`: force-include dog slots regardless of prompt match.
- `False`: suppress dog slots regardless of prompt match.

No corresponding `include_casey` flag — Casey's presence is governed by the `always_include` rule and the "is a person plausible here?" prompt check.

### 8. Safe degradation when manifest is missing or malformed

If `/data/identity/casey-berlin/manifest.json` is absent or unparseable:

- Server starts normally — no crash.
- `@casey.berlin` calls proceed with text-only prompt (current behavior).
- A single WARN log at startup surfaces the problem. Subsequent calls do not re-log per-call (noise).
- `list_models` / `get_visual_presets` responses include an `identity_pack_loaded: bool` field so the user can see the state via MCP.

## Risks / Trade-offs

- **[Upload cost grows per call]** → Identity refs are typically 200-500 KB × up to 3 images = ~1 MB extra per call. Negligible for single-user traffic; if volume spikes, switch to base64-over-already-open connection rather than re-reading from disk every time (in-memory cache after first read — already part of startup load plan).
- **[`flux-kontext-pro` is slower and more expensive per image]** → kontext-pro is $0.04 vs flux-2-max's $0.07, so not more expensive, but polling time is comparable. Acceptable. `cost_estimate` in `ProviderResult` must reflect the actual model selected (provider already does this; just verify in tests).
- **[Collage workaround for single-input FLUX models loses fidelity]** → Two refs combined into one 1024×512 collage will produce weaker identity signal than a true multi-ref model. Mitigation: prefer `flux-kontext-pro` (single best ref only) over collaging; when references exceed what a provider supports, pass only the most relevant one (e.g. `casey` before `fimme`/`sien`). A ranked "priority" field on each slot is optional — for v1 the code picks in slot declaration order.
- **[Heuristic misses context]** → Keyword matchers will mis-classify edge prompts ("a walk through my client's office" → dogs wrongly included). Mitigation: `exclude_if_prompt_matches` runs after includes; `include_dogs=False` override always available. Accept some false positives; log the resolution decision (`extra={"identity_slots": [...]}`) so review is possible.
- **[Identity drift between refs and output]** → Diffusion models interpret references loosely. The compositional rule ("not face-to-camera") also reduces how much identity a viewer can verify — by design. Accept this trade-off; the goal is "feels like Casey," not passport-photo fidelity.
- **[Privacy on provider side]** → Gemini and BFL receive personal imagery on every call. Both have data-use policies we trust enough for the current brand pipeline; this change doesn't change that surface materially, but it does *increase* what's being sent. Noted; not a blocker.
- **[Stale cache after manifest edit]** → Requires container restart to pick up new refs. Acceptable for v1; if it becomes annoying, add a small mtime check on the manifest on each call (near-free) before going full-watcher.

## Migration Plan

1. Land code changes on a feature branch; merge to `main` — CI cuts a new version automatically.
2. On the production host (`ubuntu-smurf-mirror`), add the `identity-data` volume to `compose.yaml` and recreate the container.
3. Upload the `@casey.berlin` manifest + reference images to the volume (scp over Tailscale).
4. Restart the container so the manifest is loaded; tail logs to confirm `identity_pack_loaded=True` for `@casey.berlin`.
5. Issue a smoke `generate_image` call with `context="@casey.berlin", prompt="morning walk through the forest"` and confirm: (a) provider used is `flux-kontext-pro` or `gemini`, (b) returned WebP contains recognizable identity signal, (c) `cost_estimate` reflects the model actually used.
6. Rollback: revert the container image tag; remove the volume entry from compose (or leave it — it's harmless when nothing reads it). No schema migrations, no destructive changes.

## Open Questions

- *How many reference shots per slot is enough?* Starting assumption: 2 for `casey`, 1 each for `fimme` and `sien`. Revisit after a week of output review.
- *Should the manifest live under `/data/identity/<brand>/manifest.json` or `/data/identity/manifest.json` with per-brand keys?* Per-brand file is simpler to swap one brand without touching another; going with per-brand unless a reason emerges.
- *Keyword list for the dog heuristic needs real-world tuning.* The initial list is a first draft; plan to review + prune after observing a sample of generated outputs.
- *Is a ranked `priority` field needed on slots?* Defer until we hit the "too many refs for a single-input model" case in practice.
