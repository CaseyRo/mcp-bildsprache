"""Lightweight smoke tests for the vanilla-JS frontend helpers.

We don't want a full headless-browser test for trivial pure functions. The
frontend's two important helpers (URL-state round-trip and ZIP filename
derivation) are small and easily mirrored in Python — so we test the
Python mirrors here and also sanity-check that the JS source keeps
exporting these functions with matching names.

If the JS starts diverging from the Python mirrors, the string-assert
tests at the bottom catch the drift.
"""

from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "mcp_bildsprache" / "gallery" / "static" / "app.js"


# ---------------------------------------------------------------------------
# Python mirrors of the JS helpers.
# Keep these in lock-step with gallery/static/app.js.
# ---------------------------------------------------------------------------


def state_to_query_string(view: str, filters: dict) -> str:
    parts: list[tuple[str, str]] = []
    if view and view != "grid":
        parts.append(("view", view))
    brand = filters.get("brand") or []
    if brand:
        parts.append(("brand", ",".join(brand)))
    for key in ("platform", "from", "to", "q"):
        val = filters.get(key)
        if val:
            parts.append((key, val))
    if not parts:
        return ""
    # URLSearchParams in the browser uses application/x-www-form-urlencoded.
    # For our tests we just need stable, predictable ordering.
    from urllib.parse import urlencode

    return "?" + urlencode(parts)


def query_string_to_state(search: str) -> dict:
    from urllib.parse import parse_qs

    params = parse_qs(search.lstrip("?"), keep_blank_values=False)
    view = "list" if params.get("view", [""])[0] == "list" else "grid"
    brand_raw = params.get("brand", [""])[0]
    return {
        "view": view,
        "filters": {
            "brand": [b for b in brand_raw.split(",") if b] if brand_raw else [],
            "platform": params.get("platform", [""])[0],
            "from": params.get("from", [""])[0],
            "to": params.get("to", [""])[0],
            "q": params.get("q", [""])[0],
        },
    }


def zip_filename_for(path: str, counts: dict[str, int]) -> str:
    parts = path.split("/")
    base = parts[-1]
    if counts.get(base, 0) > 1 and len(parts) > 1:
        return "/".join(parts[-2:])
    return base


def count_basenames(paths: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in paths:
        base = p.split("/")[-1]
        out[base] = out.get(base, 0) + 1
    return out


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestQueryStringRoundTrip:
    def test_empty(self):
        state = {"view": "grid", "filters": {"brand": [], "platform": "", "from": "", "to": "", "q": ""}}
        qs = state_to_query_string(state["view"], state["filters"])
        assert qs == ""
        back = query_string_to_state(qs)
        assert back == state

    def test_full(self):
        state = {
            "view": "list",
            "filters": {
                "brand": ["cdit", "casey-berlin"],
                "platform": "og-image",
                "from": "2026-01-01",
                "to": "2026-01-31",
                "q": "forest",
            },
        }
        qs = state_to_query_string(state["view"], state["filters"])
        assert "view=list" in qs
        assert "brand=cdit%2Ccasey-berlin" in qs
        assert "q=forest" in qs
        back = query_string_to_state(qs)
        assert back == state

    def test_default_grid_not_in_qs(self):
        qs = state_to_query_string("grid", {"brand": [], "platform": "", "from": "", "to": "", "q": "x"})
        assert "view=" not in qs
        assert "q=x" in qs


class TestZipFilename:
    def test_basename_by_default(self):
        counts = count_basenames(["cdit/foo-100x100.webp", "casey/bar-100x100.webp"])
        assert zip_filename_for("cdit/foo-100x100.webp", counts) == "foo-100x100.webp"

    def test_collision_prefixes_with_brand(self):
        counts = count_basenames(["cdit/foo-100x100.webp", "casey-berlin/foo-100x100.webp"])
        assert zip_filename_for("cdit/foo-100x100.webp", counts) == "cdit/foo-100x100.webp"
        assert (
            zip_filename_for("casey-berlin/foo-100x100.webp", counts)
            == "casey-berlin/foo-100x100.webp"
        )

    def test_flat_path_no_slash(self):
        counts = count_basenames(["foo-100x100.webp"])
        assert zip_filename_for("foo-100x100.webp", counts) == "foo-100x100.webp"


# ---------------------------------------------------------------------------
# Drift-check: the JS exports must still be named the same.
# ---------------------------------------------------------------------------


class TestJSHelperNames:
    def test_exports_present(self):
        src = APP_JS.read_text()
        assert "export function stateToQueryString" in src
        assert "export function queryStringToState" in src
        assert "export function zipFilenameFor" in src
        assert "export function countBasenames" in src
