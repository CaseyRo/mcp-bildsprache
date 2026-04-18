# Identity packs

This directory documents the **contract** for the `identity-data` Docker volume.
No actual identity imagery is committed to this repo — personal likeness stays
private and lives only on the production host.

## Volume layout

The container mounts `identity-data` **read-only** at `/data/identity/`. One
subdirectory per brand, each with its own `manifest.json` and the reference
image files it names:

```
/data/identity/
  casey-berlin/
    manifest.json
    casey-1.webp
    casey-2.webp
    fimme-1.webp
    sien-1.webp
  <other-brand>/
    manifest.json
    ...
```

Brand directory names match `mcp_bildsprache.slugs.BRAND_PREFIXES` values
(e.g. `@casey.berlin` → `casey-berlin`).

## Manifest shape

See [`manifest.example.json`](./manifest.example.json) for the full shape.
Summary:

- `slots` — ordered mapping of slot name → `{files: [...], tags: [...]}`.
  Declaration order is preserved and used as the resolution output order.
- `rules.always_include` — slot names that are attached whenever a person is
  plausible in the scene (i.e. the prompt does not contain a person-excluding
  marker like `"icon"`, `"flat illustration"`, `"abstract pattern"`, `"logo"`,
  `"svg"`, `"architectural detail"`).
- `rules.include_if_prompt_matches` — per-slot keyword lists. If any keyword
  appears (case-insensitive substring match) in the prompt, the slot is
  included. Exclusion overrides inclusion.
- `rules.exclude_if_prompt_matches` — per-slot keyword lists that suppress the
  slot regardless of other matches.

## Populating the volume

The volume is populated out-of-band on the production host
(`ubuntu-smurf-mirror`), typically via `scp` over Tailscale:

```bash
# from your laptop
scp -r ./casey-berlin ubuntu-smurf-mirror:/tmp/identity-staging/
ssh ubuntu-smurf-mirror
# on the host
docker run --rm \
  -v identity-data:/data/identity \
  -v /tmp/identity-staging:/src:ro \
  alpine sh -c 'cp -R /src/* /data/identity/'
docker compose -f /opt/mcp-bildsprache/compose.yaml restart
```

The server loads the manifest once at startup and caches it in process memory
for the life of the container. Edit → restart to pick up changes.

## File conventions

- WebP or JPEG, roughly 200–500 KB each (they are uploaded to the provider on
  every call that uses them).
- Square-ish or portrait orientation works best for kontext-pro; extreme
  aspect ratios are collaged sub-optimally.
- Keep the per-slot `files` list short (1–2 images). The first file wins for
  single-input providers like `flux-kontext-pro`.

## Safe degradation

- Missing directory (`/data/identity/` entirely absent): no warning, no packs
  loaded, all calls behave as text-only.
- Missing per-brand `manifest.json`: one WARN at startup, pack not loaded.
- Malformed manifest: one WARN at startup with the parse error, pack not
  loaded.
- Manifest references a file that doesn't exist on disk: one WARN per missing
  file at startup, that slot is marked unavailable and silently skipped.
