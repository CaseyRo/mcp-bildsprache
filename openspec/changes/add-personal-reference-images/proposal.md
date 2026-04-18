## Why

Generated images for the `@casey.berlin` context look on-brand stylistically but don't actually *look like Casey* — there's no identity anchor, so people and dogs in the frame are generic stand-ins. The current `presets.py` layer injects visual DNA (palette, mood, composition hints) but has no mechanism to convey personal likeness, and none of the provider integrations accept reference images today. Descriptive text alone ("man, late 30s, short brown hair…") isn't enough when the output is supposed to carry a personal brand.

A secondary correctness issue: even when a likeness *is* achieved, current outputs tend toward the LinkedIn failure mode — a person staring straight at the camera. Casey's voice calls for the opposite: the subject embedded in a scene, doing something, and never the sole focal point when others are present.

## What Changes

- **Per-provider reference-image plumbing.** Add a new optional `reference_images: list[bytes]` parameter to the provider functions that support it, plumbed through from `generate_image` in `server.py`:
  - **Gemini** — native multimodal. Append image parts as additional `inlineData` entries alongside the text prompt in `contents[0].parts`.
  - **FLUX** — switch to `flux-kontext-pro` (already declared in `FLUX_MODELS`, never invoked) when references are present; pass base64 `input_image`. For `flux-2-pro` without a kontext hop, use the `image_prompt` field.
  - **Recraft V4** — no reference-image support on the text-to-image endpoint; fall back to descriptive-only prompt augmentation when references are requested (documented limitation; provider still callable).
- **Reference-image manifest (on a Docker volume, not in-repo).** Introduce a small on-disk manifest (`manifest.json` + companion image files) loaded at server startup from a dedicated `identity-data` volume mounted at `/data/identity/<brand>/`. Kept out of the repo so personal likeness doesn't land in a public GitHub history and so reference shots can be swapped without a redeploy — the volume is the source of truth, populated by Casey via scp / Tailscale upload.
- **`@casey.berlin` identity pack.** Initial contents for `/data/identity/casey-berlin/`: Casey reference shots and the two dogs (Fimme and Sien, Stabyhoun). The repo ships only an example manifest schema + README documenting how to populate the volume; no actual identity imagery is checked in.
- **Compositional rule (scoped to `@casey.berlin`).** Extend `PRESETS["@casey.berlin"]` with a composition clause: when a person could appear in the frame, Casey is (a) never face-to-camera, (b) always depicted *doing* something in an environment, (c) not the sole focal point when other people appear. Gets prepended to the prompt like the rest of the preset.
- **Dog inclusion heuristic.** Fimme and Sien are not always present. They appear when the prompt signals an outdoor/walking/reflective/personal context (keyword heuristic in `presets.py`); they are suppressed for work/client/professional scenes. The heuristic is overrideable via an explicit flag on the MCP tool (e.g. `include_dogs: bool | None`).
- **`generate_image` tool surface.** Add optional parameters so callers can opt in/out explicitly: `reference_images` (bytes list, rare — usually auto-resolved from context), `include_dogs` (`None | bool`), and a pass-through flag to disable auto-identity if a caller really wants a generic render for `@casey.berlin`.
- **Routing update.** `route_model` gains awareness that identity-bearing requests prefer reference-capable providers (Gemini, `flux-kontext-pro`) over prompt-only providers, so a Recraft auto-route for a personal scene gets redirected.

## Capabilities

### New Capabilities

- `reference-image-input`: per-provider plumbing for passing one or more reference images into an image-generation request, plus a documented fallback behavior for providers that don't support it.
- `personal-identity-preset`: a brand-scoped identity pack (reference image manifest, compositional rule, dog-inclusion heuristic) that the orchestration layer applies automatically when the matching brand context is selected.

### Modified Capabilities

_None — no pre-existing specs in `openspec/specs/`._

## Impact

- **Code**
  - `mcp_bildsprache/providers/gemini.py` — accept `reference_images`, append as `inlineData` parts.
  - `mcp_bildsprache/providers/bfl.py` — accept `reference_images`; switch to `flux-kontext-pro` (or `flux-2-pro` `image_prompt`) when non-empty; update the internal fallback chain so reference-bearing calls don't silently fall through to a text-only model.
  - `mcp_bildsprache/providers/recraft.py` — accept and explicitly ignore (with a `logger.info` on drop) `reference_images`.
  - `mcp_bildsprache/presets.py` — extend `PRESETS["@casey.berlin"]`; add identity-manifest loader + `resolve_identity(context, prompt) -> list[bytes]`; add `route_model` adjustment for identity-bearing requests.
  - `mcp_bildsprache/server.py` — new optional parameters on `generate_image`; call into identity resolver before dispatching to provider; forward `reference_images` through.
  - `mcp_bildsprache/types.py` — any new shared dataclasses (e.g. `IdentityPack`).
- **Deployment** — new named Docker volume `identity-data` in `compose.yaml`, mounted at `/data/identity/` inside the container. Populated manually (scp / Tailscale) with the `@casey.berlin` manifest + reference images — no identity content is committed to the repo. Reference images are kept small (JPEG/WebP, ~200-500 KB each) since they are uploaded to providers on every call that uses them.
- **Repo-side** — example `manifest.json` schema + short README under `docs/identity/` explaining the volume contract (filenames, slot names, how the server picks them). This is documentation only, not loaded at runtime.
- **Cost / quota** — reference-image requests on BFL hit a different endpoint (`flux-kontext-pro`, $0.04) instead of `flux-2-max` ($0.07) or `flux-2-pro` ($0.03). Net cost may decrease or stay flat; `cost_estimate` in `ProviderResult` must reflect the actual model used.
- **Tests** — new `tests/test_presets.py` cases for identity resolution and dog heuristic; new `tests/test_providers.py` cases for each provider's reference-image branch (mock the HTTP calls); `tests/test_integration.py` for the full path including the routing adjustment.
- **Docs** — update `CLAUDE.md` "Request flow" section to show the new identity-resolution step between preset injection and provider dispatch; note the kontext-pro switch under the FLUX fallback-chain paragraph.
- **Out of scope** — identity packs for other brand contexts; non-image reference modalities (e.g. style refs from other images not tied to identity); any UI for managing the manifest.
