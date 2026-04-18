---
name: bildsprache stack location and deploy pattern
description: Key facts about where the bildsprache stack actually runs and how deploys work
type: project
---

Stack name in Komodo: `git-mcp-bildsprache-nebula`
Actual host: **nebula-1** (100.89.96.56) — NOT ubuntu-smurf-mirror despite the port comment saying otherwise
Container name: `git-mcp-bildsprache-nebula-mcp-bildsprache-1`
Network: `git-mcp-bildsprache-nebula_default`
Port mapping: 8007:8000 (host:container)

Public MCP endpoint: `https://mcp-bildsprache.cdit-dev.de/mcp` (Cloudflare tunnel via ubuntu-smurf-mirror → Tailscale → nebula-1:8007)
Tailnet gallery: `bildsprache-gallery.onca-blenny.ts.net` (docktail service)
Static images: `https://img.cdit-works.de/` (separate Cloudflare tunnel)

Deploy pattern: `build: .` (git-based, builds from source on nebula-1) — NOT a pre-built image pull.
- `km execute deploy-stack` only updates env/config without rebuilding
- To force rebuild after code change: SSH nebula-1, `cd /etc/komodo/stacks/git-mcp-bildsprache-nebula && git pull && docker compose build --no-cache && docker compose up -d`
- Or let the Komodo git webhook handle it after a push (but it may not rebuild if image exists)

MCP transport: `streamable-http` (as of v0.3.8). GET /mcp → 404 is correct; must use POST.

**Why:** Stack was migrated from ubuntu-smurf-mirror to nebula-1 at some point; CLAUDE.md port comment hadn't been updated. Rebuild required when code changes are the point.
**How to apply:** When asked to deploy or debug bildsprache, always target nebula-1, not ubuntu-smurf-mirror.
