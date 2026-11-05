# Tasks

Each task fits one session. The task that produces the done artifact is marked (done artifact).

- [x] 1. Provider: gif/svg request shape, GIF frame size, SVG timeout and `svg_usage`; tests for the request sent to OpenAI.
- [x] 2. Tool: `output`, `frame_count`, `frame_duration_ms` with validation; skip WebP; store `.gif`/`.svg`; ledger format; docstring and `list_models`; tests.
- [x] 3. Cost: `gpt-5.5` row, SVG author cost in `ai_attribution`; tests for the math.
- [x] 4. SVG CSP header on the static mount; test.
- [x] 5. Ruff, pytest, pip-audit on the exported lock; PR, green CI, squash-merge.
- [x] 6. Deploy check, one live GIF and one live SVG through the MCP tool (done artifact).
- [x] 7. Reflect: estimate vs actual, one line on the Linear card
