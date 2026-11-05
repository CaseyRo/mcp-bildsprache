# Tasks

Each task fits one session. The task that produces the done artifact is marked (done artifact).

- [x] 1. Verify the model ids against `/v1/models` (gpt-image-2.6-flare, gpt-image-2.6-sunburst, 5 Nov 2026)
- [x] 2. Add both ids to provider, server literal, cost table, list_models text and docs
- [x] 3. Tests: provider sends the id, 2.6 accepts xhigh/max and transparent, list_models and protocol enum list it
- [x] 4. PR, green CI, merge (auto_merge: true for mcp-fleet) (done artifact)
- [x] 5. After the release: one live render with gpt-image-2.6-flare through generate_image; attribution and ledger show the id
- [x] 6. Reflect: estimate vs actual, one line on the Linear card
