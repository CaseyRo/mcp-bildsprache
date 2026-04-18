## ADDED Requirements

### Requirement: Identity manifest loaded from a Docker volume at startup

The server SHALL load a per-brand identity manifest from `/data/identity/<brand-dir>/manifest.json` at startup and cache it in process memory for the lifetime of the container.

#### Scenario: Manifest present and valid

- **WHEN** the server starts with a valid `/data/identity/casey-berlin/manifest.json` and all referenced image files present
- **THEN** an in-memory `IdentityPack` for `@casey.berlin` MUST be available to the orchestration layer
- **AND** a single `INFO` log MUST record `identity_pack_loaded=True brand="@casey.berlin" slots=[...]`

#### Scenario: Manifest missing

- **WHEN** the server starts and `/data/identity/casey-berlin/manifest.json` does not exist
- **THEN** the server MUST start normally
- **AND** exactly one `WARN` log MUST be emitted at startup naming the missing path
- **AND** `@casey.berlin` calls MUST proceed with text-only prompts
- **AND** no per-call warnings MUST be emitted for this condition

#### Scenario: Manifest malformed

- **WHEN** the manifest file exists but fails to parse as JSON or fails schema validation
- **THEN** the server MUST start normally
- **AND** exactly one `WARN` log MUST be emitted at startup with the parse/validation error
- **AND** `@casey.berlin` calls MUST proceed with text-only prompts

#### Scenario: Manifest references a missing image file

- **WHEN** the manifest references an image file that does not exist on disk
- **THEN** the server MUST start normally with that slot marked unavailable
- **AND** exactly one `WARN` log MUST be emitted per missing file at startup
- **AND** resolution MUST skip the unavailable slot silently at call time

### Requirement: Manifest layout is per-brand, not monolithic

Each brand's identity pack SHALL live in its own directory under `/data/identity/`, with a separate `manifest.json` per brand. The server MUST NOT read a single keyed monolithic manifest.

#### Scenario: Per-brand directory layout

- **WHEN** the server scans `/data/identity/` at startup
- **THEN** it MUST treat each subdirectory as a separate brand pack (e.g. `casey-berlin/`)
- **AND** each subdirectory MUST contain its own `manifest.json`

### Requirement: Identity resolution selects slots based on prompt keywords

The server SHALL implement a pure function `resolve_identity(pack, prompt) -> list[Path]` that returns the list of reference image paths to attach for a given prompt, using declarative rules from the manifest.

#### Scenario: Primary slot always attached when a person is plausible

- **WHEN** resolving identity for a prompt that does not contain any person-excluding markers (e.g. "icon", "abstract pattern", "flat illustration no people", "logo", "architectural detail")
- **THEN** every slot listed in `rules.always_include` MUST appear in the result

#### Scenario: Primary slot omitted for person-excluding prompts

- **WHEN** resolving identity for a prompt containing a person-excluding marker (e.g. "flat icon", "abstract geometric pattern")
- **THEN** the result MUST be an empty list

#### Scenario: Conditional slots included when prompt matches include keywords

- **WHEN** resolving a prompt containing any keyword in a slot's `include_if_prompt_matches` list AND no keyword in that slot's `exclude_if_prompt_matches` list
- **THEN** that slot MUST appear in the result

#### Scenario: Exclude rule overrides include rule

- **WHEN** a prompt matches both an include and an exclude keyword for the same slot
- **THEN** the slot MUST NOT appear in the result

#### Scenario: Keyword matching is case-insensitive and substring-based

- **WHEN** matching a keyword `walk` against a prompt `Morning Walking in the Forest`
- **THEN** the match MUST succeed
- **AND** matching MUST NOT require whole-word boundaries

#### Scenario: Resolution is deterministic

- **WHEN** `resolve_identity` is called twice with the same pack and the same prompt
- **THEN** the returned list MUST be identical, including order

### Requirement: Casey identity pack ships with the documented slot set

The default manifest for `@casey.berlin` SHALL declare exactly three slots — `casey`, `fimme`, `sien` — with `casey` in `always_include` and `fimme`/`sien` governed by walking/outdoor/reflective include keywords and work/client exclude keywords.

#### Scenario: Casey slot on a personal prompt

- **WHEN** resolving identity for prompt `"late afternoon coffee at my desk, thinking about the week"` with the default manifest
- **THEN** the result MUST include the `casey` slot
- **AND** the result MUST NOT include `fimme` or `sien` (no walking/outdoor keywords)

#### Scenario: Dogs included on an outdoor prompt

- **WHEN** resolving for prompt `"morning walk through the forest with the dogs"` with the default manifest
- **THEN** the result MUST include `casey`, `fimme`, and `sien`

#### Scenario: Dogs excluded for a client scene

- **WHEN** resolving for prompt `"walking to a client meeting in the office building"` with the default manifest
- **THEN** the result MUST include `casey`
- **AND** MUST NOT include `fimme` or `sien` (exclude keywords `client`, `meeting`, `office` win)

### Requirement: generate_image exposes identity overrides

The `generate_image` MCP tool SHALL expose two optional parameters controlling identity behavior: `reference_images: list[bytes] | None = None` (full override) and `include_dogs: bool | None = None` (dog-slot override).

#### Scenario: Caller supplies reference_images directly

- **WHEN** `generate_image` is called with a non-empty `reference_images` list
- **THEN** the server MUST skip `resolve_identity` entirely
- **AND** pass the caller's references through to the provider unchanged

#### Scenario: include_dogs=True forces dog slots

- **WHEN** `generate_image` is called with `context="@casey.berlin"` and `include_dogs=True`
- **THEN** the resolver MUST include the `fimme` and `sien` slots regardless of prompt keyword matches

#### Scenario: include_dogs=False suppresses dog slots

- **WHEN** `generate_image` is called with `context="@casey.berlin"` and `include_dogs=False`
- **THEN** the resolver MUST exclude the `fimme` and `sien` slots regardless of prompt keyword matches

#### Scenario: include_dogs=None uses the heuristic

- **WHEN** `generate_image` is called with `include_dogs=None` (default)
- **THEN** the resolver MUST apply `include_if_prompt_matches` / `exclude_if_prompt_matches` rules as declared

### Requirement: Composition clause is prepended to prompts for @casey.berlin

When a person is plausibly in the scene for `@casey.berlin` (i.e. identity resolution returned a non-empty list including the primary slot), the prompt-assembly layer SHALL prepend a compositional clause stating the subject is embedded in the scene, never face-to-camera, and never the sole focal point when other people are present.

#### Scenario: Composition clause present for personal-context prompts

- **WHEN** `generate_image` is called with `context="@casey.berlin"` and a prompt that resolves to a non-empty identity slot list
- **THEN** the final enhanced prompt sent to the provider MUST include the compositional clause

#### Scenario: Composition clause absent for person-excluding prompts

- **WHEN** `generate_image` is called with `context="@casey.berlin"` and a prompt containing a person-excluding marker (identity resolution returns empty)
- **THEN** the composition clause MUST NOT be added

#### Scenario: Composition clause is scoped to @casey.berlin

- **WHEN** `generate_image` is called with any `context` other than `@casey.berlin`
- **THEN** the composition clause MUST NOT be added regardless of prompt content

### Requirement: Routing is reference-aware

`route_model` SHALL accept a `has_references: bool` parameter. When true, routing MUST prefer reference-capable providers and MUST NOT fall back to text-only models on failure.

#### Scenario: Reference-bearing request avoids Recraft auto-route

- **WHEN** `route_model(context, platform, model_hint=None, has_references=True)` would otherwise auto-select Recraft
- **THEN** it MUST instead select FLUX (kontext-pro)
- **AND** this override MUST NOT apply when `model_hint` pins Recraft explicitly

#### Scenario: Reference-bearing FLUX chain excludes text-only models

- **WHEN** a FLUX call with references fails and falls back
- **THEN** the fallback target MUST be either `flux-2-pro` (with `image_prompt`) or Gemini
- **AND** `flux-2-max` MUST NOT be attempted

#### Scenario: Non-reference request routing is unchanged

- **WHEN** `route_model(..., has_references=False)` is called
- **THEN** the returned provider key MUST match the pre-change behavior for the same inputs

### Requirement: Observability of identity resolution

Each `generate_image` call that applies an identity pack SHALL emit a structured log record naming the slots that were resolved.

#### Scenario: Log record contains slot list

- **WHEN** `generate_image` resolves a non-empty identity list for a call
- **THEN** exactly one log record at `INFO` level MUST be emitted
- **AND** it MUST include fields `brand`, `slots` (list of slot names), `provider`, and `has_include_dogs_override`

### Requirement: Server introspection surfaces identity pack state

The `list_models` and `get_visual_presets` MCP tools SHALL each report whether an identity pack is currently loaded for each brand.

#### Scenario: list_models reports pack state

- **WHEN** `list_models` is called
- **THEN** the response MUST include a field `identity_packs` mapping brand → `bool` (loaded state)

#### Scenario: get_visual_presets reports pack state

- **WHEN** `get_visual_presets(context="@casey.berlin")` is called
- **THEN** the returned object MUST include `identity_pack_loaded: bool`

### Requirement: Identity imagery is never exposed on the public static mount

Reference images under `/data/identity/` MUST NOT be reachable via the `img.cdit-works.de` static mount or any other HTTP surface of the server.

#### Scenario: Static mount scoped to /data/images

- **WHEN** the HTTP app is constructed
- **THEN** the static files mount MUST point only at `/data/images`
- **AND** no route MUST resolve under `/data/identity/` via any mount or endpoint
