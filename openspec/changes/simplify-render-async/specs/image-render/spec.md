# Image Render

## ADDED Requirements

### Requirement: Rendering never blocks the event loop

The server SHALL run all CPU-bound and blocking-IO work (image post-processing, disk writes, gallery index walks) off the asyncio event loop, so no render or maintenance task starves concurrent MCP requests.

#### Scenario: Render post-processing in progress

- **WHEN** a `generate_image` render is post-processing a large image (resize/crop/WebP/EXIF)
- **THEN** other MCP tool calls continue to be served without `-32001` timeouts

#### Scenario: Gallery reindex tick

- **WHEN** the periodic gallery reindex walks the image volume
- **THEN** the event loop remains responsive to MCP requests for the duration of the walk

### Requirement: Long renders dispatch as pollable background work

A render that exceeds the inline budget SHALL return a task/job handle the caller can poll, rather than holding the request open past the gateway timeout.

#### Scenario: Slow render

- **WHEN** `generate_image` is invoked and the render exceeds the inline budget
- **THEN** the tool returns a handle immediately (well under the gateway timeout)
- **AND** the caller retrieves the result by polling
- **AND** the render completes and is stored regardless of the client connection lifetime

### Requirement: Only dispatched providers ship in the tree

The codebase SHALL NOT carry provider modules or fallback maps that are unreachable at the dispatcher.

#### Scenario: A provider is not dispatched

- **WHEN** a provider is disabled at the dispatcher (e.g. FLUX, Recraft)
- **THEN** its module is absent from the tree
- **AND** no fallback map or routing branch references it
