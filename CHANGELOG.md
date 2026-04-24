# Changelog

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
