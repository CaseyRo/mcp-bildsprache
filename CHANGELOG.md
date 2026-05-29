# Changelog

## [0.3.34] - 2026-05-29

- fix(gemini): constrain render size so calls fit the MCP portal budget


## [0.3.32] - 2026-05-11

- feat(openai): serialise dispatch + structured rate-limit log


## [0.3.31] - 2026-05-11

- fix(gallery): 0×0 dimensions, missing platform, broken view toggle


## [0.3.30] - 2026-05-09

- fix: expose docktail service on HTTPS/443 for bildsprache gallery


## [0.3.29] - 2026-05-09

- fix(gallery): drive sub-app lifespan from parent so reindex actually fires


## [0.3.28] - 2026-05-09

- docs(config): clarify disabled-but-recognised env vars (BFL/Recraft)


## [0.3.27] - 2026-05-09

- fix(openai): per-model size validation, edits-endpoint refs, no fallback


## [0.3.26] - 2026-05-09

- feat(diagram): add generate_diagram tool + gallery Tailnet host


## [0.3.25] - 2026-05-09

- feat(brand): casey + yorizon collapse, OpenAI-default raster, FLUX/Recraft disabled


## [0.3.24] - 2026-05-08

- chore(repo): correct deploy host docs + add dependabot auto-merge


## [0.3.22] - 2026-05-07

- ops(docker): add log rotation (10m/3 files) to cap unbounded json-file logs


## [0.3.21] - 2026-04-25

- chore(compose): pass OPENAI_API_KEY + model overrides to container


## [0.3.20] - 2026-04-25

- feat(brands): canonical bare-slug aliasing across the fleet (CDI-1041)


## [0.3.19] - 2026-04-24

- feat(hygiene): /health + fail-fast + structured cost log (CDI-1014 §11)


## [0.3.18] - 2026-04-24

- ci: shared-contract drift check (CDI-1014 §2.1)


## [0.3.17] - 2026-04-24

- feat(routing): wire openai + draft flag into generate_image (CDI-1014 §5)


## [0.3.16] - 2026-04-24

- feat(providers): OpenAI gpt-image-2 provider (CDI-1014 §4)


## [0.3.15] - 2026-04-24

- feat(attribution): wire ai_attribution v1 into generate_image (CDI-1014 §3)


## [0.3.14] - 2026-04-24

- fix: read __version__ from package metadata instead of hardcoding


## [0.3.13] - 2026-04-20

- ci(deps): enable Dependabot weekly updates


## [0.3.12] - 2026-04-20

- chore(pkg): add __main__.py and py.typed per MCP Server Standards


## [0.3.11] - 2026-04-19

- chore(deps): refresh uv.lock to clear Dependabot alerts


## [0.3.10] - 2026-04-19

- fix(security): use SecretStr for MCP key + Keycloak client secret


## [0.3.9] - 2026-04-18

- feat(gallery): port frontend to CDiT MX-Brutalist design system


## [0.3.8] - 2026-04-18

- infra: add docktail labels and gallery/identity env vars to compose


## [0.3.7] - 2026-04-18

- chore: resolve merge conflicts and clean up post-parallel-build state


## [0.3.5] - 2026-04-18

- fix(auth): enable bearer auth without Keycloak dependency


## [0.3.4] - 2026-04-10

- fix: register webp/avif mime types for correct Content-Type headers


## [0.3.3] - 2026-04-10

- fix: mount static files at root path for img.cdit-works.de compatibility


## [0.3.2] - 2026-04-10

- fix: use FastMCP 3.2.x http_app() API for static file mounting


## [0.2.0] - 2026-04-10

### Changed
- Bumped FastMCP dependency to >=3.2.2
- Added [image] domain prefix to all tool docstrings
- Added Literal enum constraints to context and platform parameters

### Added
- Automated version bump and release CI via GitHub Actions
- CHANGELOG.md for tracking changes
