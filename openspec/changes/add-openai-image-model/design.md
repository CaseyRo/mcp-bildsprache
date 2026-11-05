# Design: Offer OpenAI's GPT Image 2.6 models in Bildsprache

## Approach
Add the two ids wherever the 2.5 ids are named, and widen the "2.5 options" check (xhigh/max quality, transparent background) from the 2.5 prefix to 2.5 and 2.6. Model pages and resellers list the same controls and the same token rates as 2.5 ($5 text in, $8 image in, $30 image out per 1M), so the cost rows copy the 2.5 rows.

## Touches
| System or repo | Change |
| --- | --- |
| `providers/openai.py` | `IMAGE_MODELS`, the option check, legacy rates |
| `server.py` | `_ModelLiteral`, tool docstrings, `list_models` text |
| `_shared/provider_costs.v1.yaml` | two rows |
| tests | provider, protocol enum, list_models, attribution |
| CLAUDE.md | one line |

## Decisions taken
- Per-call only; no default change (problem.md, out of scope).
- Rates copied from 2.5: OpenRouter and the launch coverage give $8 / $30 per 1M, same as 2.5.

## Decisions deferred
- Moving the default split to 2.6: a bake-off card, Casey decides.

## Risks
- The portal caches the tool catalog, so the new enum values may need a catalog refresh before a client can send them. Mitigation: check after deploy with one live render.
