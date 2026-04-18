# Gallery static assets

These files are served from `/gallery/static/` and are shipped as-is in the
container image. There is no build step — edit the files directly.

## Vendored third-party

### fflate

- File: `fflate.min.js`
- Version: **0.8.2**
- Source: <https://unpkg.com/fflate@0.8.2/umd/index.js>
- License: MIT
- SHA-256: `c3b34f2e9f5e74d4d7d64e01cac7a0c01954c6c406414d42185c7b53d6875ddf`

Verify with:

```bash
shasum -a 256 mcp_bildsprache/gallery/static/fflate.min.js
```

To upgrade, fetch the desired version's `umd/index.js` from unpkg and
update both the checksum and the version line above.
