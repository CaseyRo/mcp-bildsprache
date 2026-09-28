"""mcp-bildsprache package."""

import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("mcp-bildsprache")
except PackageNotFoundError:
    __version__ = "unknown"

try:
    # Baked by the Dockerfile's `rev` stage; releases are tags, so pyproject's
    # version lags and the commit is what identifies a deploy.
    GIT_COMMIT = (
        os.environ.get("GIT_COMMIT") or Path("/app/.git_commit").read_text().strip()
    )
except OSError:
    GIT_COMMIT = "unknown"
