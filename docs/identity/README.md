# Identity packs

This directory documents the **contract** for the `identity-data` Docker volume.
No actual identity imagery is committed to this repo — personal likeness stays
private and lives only on the host that runs the server.

## Volume layout

The container mounts `identity-data` **read-only** at `/data/identity/`. One
subdirectory per brand, each with its own `manifest.json` and the reference
image files it names:

```
/data/identity/
  <brand-dir>/
    manifest.json
    <slot>-1.webp
    <slot>-2.webp
  <other-brand>/
    manifest.json
    ...
```

Brand directory names match `mcp_bildsprache.slugs.BRAND_PREFIXES` values.

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

Copy the brand directories into the `identity-data` volume out-of-band, for
example with a throwaway container:

```bash
docker run --rm \
  -v identity-data:/data/identity \
  -v "$PWD/identity-staging":/src:ro \
  alpine sh -c 'cp -R /src/* /data/identity/'
docker compose restart
```

The server loads the manifests once at startup and caches them in process
memory for the life of the container. Edit, then restart to pick up changes.

## Slot names

Slot names are free-form, with one exception: the slots that the
`include_dogs` override controls are listed in
`mcp_bildsprache/identity.py` (`DOG_SLOT_NAMES`), so a manifest that wants
that override must use those names. Every other slot follows
`include_people`.

## File conventions

- WebP or JPEG, roughly 200 to 500 KB each (they are uploaded to the provider
  on every call that uses them).
- Keep the per-slot `files` list short (1 or 2 images).

## Safe degradation

- Missing directory (`/data/identity/` entirely absent): no warning, no packs
  loaded, all calls behave as text-only.
- Missing per-brand `manifest.json`: one WARN at startup, pack not loaded.
- Malformed manifest: one WARN at startup with the parse error, pack not
  loaded.
- Manifest references a file that doesn't exist on disk: one WARN per missing
  file at startup, that slot is marked unavailable and silently skipped.
