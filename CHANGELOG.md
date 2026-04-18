# Changelog

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
