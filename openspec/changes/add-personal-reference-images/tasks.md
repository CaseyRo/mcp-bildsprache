## 1. Groundwork — types, config, volume

- [x] 1.1 Add `IdentityPack` and `IdentitySlot` dataclasses to `mcp_bildsprache/types.py`
- [x] 1.2 Add identity-related settings to `mcp_bildsprache/config.py`: `identity_dir: Path = Path("/data/identity")` and `identity_enabled: bool = True`
- [x] 1.3 Add the `identity-data` named volume to `compose.yaml` mounted read-only at `/data/identity/`
- [x] 1.4 Create `docs/identity/README.md` documenting the volume contract (per-brand dir, `manifest.json` schema, filename conventions, how to populate via scp over Tailscale)
- [x] 1.5 Ship an example manifest at `docs/identity/manifest.example.json` showing the default `@casey.berlin` slot set (no actual imagery)

## 2. Manifest loader

- [x] 2.1 Create `mcp_bildsprache/identity.py` with `load_identity_packs(root: Path) -> dict[str, IdentityPack]`
- [x] 2.2 Implement JSON schema validation (pydantic model matching the design doc shape) with `slots`, `rules.always_include`, `rules.include_if_prompt_matches`, `rules.exclude_if_prompt_matches`
- [x] 2.3 Implement per-brand directory scan under `/data/identity/*/manifest.json` (no monolithic keyed file)
- [x] 2.4 Implement safe degradation: missing dir → no packs (no warn), missing manifest → WARN once, unparseable manifest → WARN once with error, missing referenced file → WARN per file and mark slot unavailable
- [x] 2.5 Emit a single `INFO` `identity_pack_loaded=True brand=... slots=[...]` per successfully loaded pack
- [x] 2.6 Cache loaded packs in process memory (module-level dict, populated at startup)

## 3. Identity resolution

- [x] 3.1 Implement `resolve_identity(pack: IdentityPack, prompt: str) -> list[Path]` as a pure function in `identity.py`
- [x] 3.2 Implement person-excluding marker check (`"icon"`, `"flat illustration"`, `"abstract pattern"`, `"logo"`, `"architectural detail"`, …) — on match, return `[]`
- [x] 3.3 Implement `always_include` slot attachment when person is plausible
- [x] 3.4 Implement `include_if_prompt_matches` scan (case-insensitive substring, OR across keywords)
- [x] 3.5 Implement `exclude_if_prompt_matches` override (exclude wins over include)
- [x] 3.6 Guarantee deterministic output order (manifest declaration order)
- [x] 3.7 Expose `resolve_identity_for_call(pack, prompt, include_dogs: bool | None) -> list[Path]` wrapper that handles the `include_dogs` override

## 4. Provider — Gemini reference support

- [ ] 4.1 Extend `generate_gemini` signature with `reference_images: list[bytes] | None = None`
- [ ] 4.2 When non-empty: probe each bytes blob for mime type (Pillow) and append an `inlineData` part to `contents[0].parts` in list order
- [ ] 4.3 Raise `ValueError` naming the offending list index when a blob's mime can't be determined
- [ ] 4.4 Preserve text-only request shape when `reference_images` is `None` or empty

## 5. Provider — BFL/FLUX reference support

- [ ] 5.1 Extend `generate_bfl` signature with `reference_images: list[bytes] | None = None`
- [ ] 5.2 Implement reference-bearing fallback chain: `flux-kontext-pro → flux-2-pro (image_prompt) → Gemini` (the last hop is handled in `server.py`'s `FALLBACKS` map, not here)
- [ ] 5.3 Ensure `flux-2-max` is **not** attempted when `reference_images` is non-empty
- [ ] 5.4 Implement `_collage(images: list[bytes]) -> bytes` that builds a 1×N horizontal grid with Pillow when >1 reference is supplied for a single-input model
- [ ] 5.5 Send base64-encoded `input_image` on the kontext-pro request and `image_prompt` on the flux-2-pro request
- [ ] 5.6 Update `ProviderResult.cost_estimate` and `.model` to reflect the model that actually succeeded
- [ ] 5.7 Log collage events with source count and dimensions

## 6. Provider — Recraft log-and-drop

- [ ] 6.1 Extend `generate_recraft` signature with `reference_images: list[bytes] | None = None`
- [ ] 6.2 When non-empty, emit a single `INFO` log (`recraft: dropped N reference image(s) — provider does not support references`) and proceed with the text-only request unchanged

## 7. Routing

- [ ] 7.1 Extend `route_model(context, platform, model_hint, has_references=False)` in `presets.py`
- [ ] 7.2 When `has_references=True` and an auto-route would land on Recraft (and `model_hint` isn't pinning Recraft), redirect to `flux`
- [ ] 7.3 Update `FALLBACKS` map in `server.py` to branch on reference-bearing calls: `flux → gemini` (both reference-capable), `gemini → flux`
- [ ] 7.4 Confirm non-reference routing is byte-for-byte unchanged against existing tests

## 8. `server.py` — tool surface & orchestration

- [ ] 8.1 Add optional `reference_images: list[bytes] | None = None` and `include_dogs: bool | None = None` parameters to `generate_image`
- [ ] 8.2 Between preset injection and provider dispatch: look up identity pack for the call's context; if caller supplied `reference_images`, use them directly; otherwise call `resolve_identity_for_call` and read files from disk
- [ ] 8.3 Cache reference-image bytes after first read per process (module-level `dict[Path, bytes]`)
- [ ] 8.4 Pass `has_references=bool(refs)` into `route_model` and `refs` into the chosen provider
- [ ] 8.5 Prepend the composition clause to `enhanced_prompt` only when context is `@casey.berlin` and `resolve_identity` returned a non-empty list
- [ ] 8.6 Emit the per-call structured log (`brand`, `slots`, `provider`, `has_include_dogs_override`)
- [ ] 8.7 Extend `list_models` response with `identity_packs: dict[str, bool]`
- [ ] 8.8 Extend `get_visual_presets` response with `identity_pack_loaded: bool` per brand

## 9. Preset — @casey.berlin composition clause

- [ ] 9.1 Add the compositional clause string to `PRESETS["@casey.berlin"]` in `presets.py` (embedded in the scene, never face-to-camera, never sole focal point in group shots)
- [ ] 9.2 Ensure it's only appended when identity resolution produced a non-empty list (gated in `server.py`, not hard-coded into the preset)

## 10. HTTP hygiene — keep /data/identity off the public mount

- [ ] 10.1 Audit `_mount_static_files` in `server.py` — confirm only `/data/images` is mounted
- [ ] 10.2 Add a regression test asserting that no static mount resolves under `/data/identity/`

## 11. Tests

- [x] 11.1 `tests/test_identity.py` — manifest loader: valid / missing dir / missing manifest / malformed JSON / missing image files (one case each)
- [x] 11.2 `tests/test_identity.py` — `resolve_identity` cases: personal prompt → casey only; outdoor prompt → casey+dogs; client prompt → casey only (exclude wins); person-excluding marker → empty; deterministic order
- [x] 11.3 `tests/test_identity.py` — `include_dogs=True/False/None` override semantics
- [ ] 11.4 `tests/test_providers.py` — Gemini: references become `inlineData` parts in order; unsupported mime raises pre-request; text-only unchanged
- [ ] 11.5 `tests/test_providers.py` — BFL: single ref → kontext-pro; multi-ref → collage → kontext-pro; kontext-pro fail → flux-2-pro with image_prompt; flux-2-max never called when refs present; cost/model reflect actual success
- [ ] 11.6 `tests/test_providers.py` — Recraft: log-and-drop, text-only request unchanged
- [ ] 11.7 `tests/test_presets.py` — `route_model` reference-aware: Recraft auto-route redirected; explicit Recraft hint respected; non-reference routing byte-for-byte unchanged
- [ ] 11.8 `tests/test_presets.py` — composition clause: present for `@casey.berlin` personal prompt, absent for person-excluding prompt, absent for non-`@casey.berlin` contexts
- [ ] 11.9 `tests/test_integration.py` — end-to-end `generate_image` happy path with a stubbed identity pack (mock provider HTTP), asserting the full log record and the final enhanced prompt
- [ ] 11.10 `tests/test_integration.py` — regression test that no route resolves under `/data/identity/`

## 12. Docs

- [ ] 12.1 Update `CLAUDE.md` "Request flow" block to show the new identity-resolution step between preset injection and provider dispatch
- [ ] 12.2 Update `CLAUDE.md` FLUX fallback-chain paragraph to note the reference-aware variant (`flux-kontext-pro → flux-2-pro → gemini`) and that `flux-2-max` is skipped for reference-bearing calls
- [ ] 12.3 Add a short "Identity packs" subsection under "Brand presets" in `CLAUDE.md` pointing at `/data/identity/` and `docs/identity/README.md`
- [ ] 12.4 Cross-link the new MCP tool parameters (`reference_images`, `include_dogs`) in README tool list

## 13. Deploy

- [ ] 13.1 Merge to `main`; let CI cut a release tag + image
- [ ] 13.2 On `ubuntu-smurf-mirror`: pull new image, add `identity-data` volume to the running compose stack, recreate container
- [ ] 13.3 scp over Tailscale: upload `@casey.berlin` manifest + reference images to the volume
- [ ] 13.4 Restart container; confirm startup log shows `identity_pack_loaded=True brand="@casey.berlin" slots=["casey","fimme","sien"]`
- [ ] 13.5 Smoke test: `generate_image context="@casey.berlin" prompt="morning walk through the forest"` — verify provider is `flux-kontext-pro` or `gemini`, cost reflects that, and the result carries recognizable identity
- [ ] 13.6 Smoke test: `generate_image context="@casey.berlin" prompt="a flat icon of a coffee cup"` — verify identity resolution returned empty, no composition clause, no references sent
