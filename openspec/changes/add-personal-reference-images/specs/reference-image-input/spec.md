## ADDED Requirements

### Requirement: Providers accept an optional reference-images parameter

Every provider function in `mcp_bildsprache.providers` SHALL accept an optional `reference_images: list[bytes] | None = None` parameter. When `None` or empty, the provider SHALL behave identically to today (text-only generation).

#### Scenario: Provider called without references behaves unchanged

- **WHEN** any provider function is invoked with `reference_images=None`
- **THEN** the outbound request MUST match the current text-only request shape
- **AND** the returned `ProviderResult.model` MUST be the same model that would have been used without the parameter

#### Scenario: Provider called with empty list behaves unchanged

- **WHEN** any provider function is invoked with `reference_images=[]`
- **THEN** the provider MUST treat the call as text-only
- **AND** MUST NOT switch endpoints or models based on the empty list

### Requirement: Gemini provider attaches references as inline image parts

When `reference_images` is non-empty, the Gemini provider SHALL append each image to `contents[0].parts` as an additional `inlineData` entry, preserving the existing text part.

#### Scenario: Gemini receives two references

- **WHEN** `generate_gemini(prompt, width, height, reference_images=[img1, img2])` is called
- **THEN** the outbound payload MUST contain exactly three parts in `contents[0].parts`: one text part followed by two `inlineData` parts in the same order as the input list
- **AND** each `inlineData` part MUST carry the correct `mimeType` (probed from bytes) and base64-encoded `data`

#### Scenario: Gemini reference with unsupported mime type is rejected

- **WHEN** `generate_gemini` is called with a reference whose bytes cannot be probed to a supported image mime type
- **THEN** the provider MUST raise a `ValueError` before making an HTTP call
- **AND** the error message MUST name the offending index in the list

### Requirement: FLUX provider routes reference-bearing requests to a reference-capable model

When `reference_images` is non-empty, the BFL provider SHALL route to a reference-capable FLUX model. The preferred model is `flux-kontext-pro` (single input). The fallback chain for reference-bearing calls is `flux-kontext-pro → flux-2-pro (image_prompt)`. `flux-2-max` MUST NOT be invoked for reference-bearing calls.

#### Scenario: Single reference routed to flux-kontext-pro

- **WHEN** `generate_bfl(prompt, w, h, reference_images=[img])` is called with no explicit `model` hint
- **THEN** the first attempted endpoint MUST be `flux-kontext-pro`
- **AND** the request payload MUST include `input_image` as a base64-encoded string of `img`

#### Scenario: Multiple references to a single-input model are collaged

- **WHEN** `generate_bfl(prompt, w, h, reference_images=[a, b, c])` is called
- **THEN** the provider MUST combine the references into a single collage image in manifest-declared order
- **AND** submit that collage as `input_image` to `flux-kontext-pro`
- **AND** the log record for this call MUST include the collage dimensions and source count

#### Scenario: flux-kontext-pro failure falls through to flux-2-pro with image_prompt

- **WHEN** `flux-kontext-pro` raises an error and at least one reference is present
- **THEN** the next attempted model MUST be `flux-2-pro`
- **AND** the first reference MUST be passed via the `image_prompt` field
- **AND** `flux-2-max` MUST NOT be attempted

### Requirement: Recraft provider ignores references explicitly and observably

When `reference_images` is non-empty, the Recraft provider SHALL proceed with a text-only request AND SHALL emit a single `INFO`-level log record noting the references were dropped.

#### Scenario: Recraft drops references and logs

- **WHEN** `generate_recraft(prompt, w, h, reference_images=[img])` is called
- **THEN** the outbound request MUST be identical to today's text-only request
- **AND** exactly one log record MUST be emitted at `INFO` level containing the count of references dropped and the reason
- **AND** the returned `ProviderResult.model` MUST be `recraft-v4`

### Requirement: Cost estimate reflects the model actually used

`ProviderResult.cost_estimate` SHALL reflect the cost of the model that actually produced the image, not the model originally requested.

#### Scenario: Fallback from kontext-pro updates cost estimate

- **WHEN** `generate_bfl` attempts `flux-kontext-pro` (cost `$0.04`), fails, and succeeds on `flux-2-pro` (cost `$0.03`)
- **THEN** the returned `ProviderResult.cost_estimate` MUST be `$0.03`
- **AND** `ProviderResult.model` MUST be `flux-2-pro`
