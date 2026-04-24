"""mcp-bildsprache package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-bildsprache")
except PackageNotFoundError:
    __version__ = "unknown"
