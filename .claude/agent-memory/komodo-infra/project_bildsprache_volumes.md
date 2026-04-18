---
name: bildsprache Docker volumes
description: Volume names and host-side paths for bildsprache on nebula-1
type: project
---

All volumes on nebula-1, under `/var/lib/docker/volumes/`:

| Volume name | Host path | Container mount | RW |
|---|---|---|---|
| `git-mcp-bildsprache-nebula_fastmcp-data` | `…/fastmcp-data/_data` | `/data/fastmcp` | RW |
| `git-mcp-bildsprache-nebula_images-data` | `…/images-data/_data` | `/data/images` | RW |
| `git-mcp-bildsprache-nebula_identity-data` | `…/identity-data/_data` | `/data/identity` | **RO** |

Identity upload target for Casey's scp: `/var/lib/docker/volumes/git-mcp-bildsprache-nebula_identity-data/_data/casey-berlin/`

**Why:** These paths are needed for maintenance, backup, and Casey's identity image uploads.
**How to apply:** Never touch fastmcp-data or images-data volumes. Identity volume is RO inside container but writable from host.
