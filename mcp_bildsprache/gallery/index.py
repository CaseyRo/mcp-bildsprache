"""In-memory sidecar index for the gallery.

Walks `<data_dir>/**/*.json`, parses each sidecar JSON, and holds the
result in a list of :class:`GalleryEntry` plus a path-keyed dict for
O(1) deep-link lookups. The index is cheap enough that a full rescan
runs on every reindex tick (startup + timer + POST /api/reindex).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class GalleryEntry:
    """One indexed image sourced from a sidecar JSON."""

    path: str  # relative to data_dir, POSIX separators (e.g. "cdit/foo-1200x630.webp")
    hosted_url: str
    brand: str  # top-level directory under data_dir
    prompt: str
    prompt_lower: str  # pre-lowered for fast case-insensitive substring search
    model: str
    cost_estimate: str
    width: int
    height: int
    platform: str | None
    file_size: int
    created_at: datetime

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize for JSON responses (ISO-8601 for datetime, drop internal fields)."""
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        # prompt_lower is an internal optimization; not part of the public payload
        data.pop("prompt_lower", None)
        return data


def _parse_dimensions(raw: Any) -> tuple[int | None, int | None]:
    """Best-effort extraction of (width, height) from a sidecar field."""
    if not raw:
        return None, None
    if isinstance(raw, str) and "x" in raw.lower():
        try:
            w_s, h_s = raw.lower().split("x", 1)
            return int(w_s), int(h_s)
        except (ValueError, IndexError):
            return None, None
    if isinstance(raw, dict):
        w = raw.get("width")
        h = raw.get("height")
        try:
            return int(w) if w is not None else None, int(h) if h is not None else None
        except (TypeError, ValueError):
            return None, None
    return None, None


def _parse_created_at(raw: Any, sidecar_path: Path) -> datetime:
    """Parse `generated_at`/`created_at` ISO-8601; fall back to file mtime."""
    if isinstance(raw, str):
        try:
            # Python 3.11+: fromisoformat accepts `Z` as well (via replace).
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(sidecar_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.now(timezone.utc)


def _derive_hosted_url(
    sidecar: dict[str, Any],
    relative_path: str,
    public_base_url: str,
) -> str:
    """Prefer sidecar-stored hosted_url; fall back to base + relative."""
    stored = sidecar.get("hosted_url")
    if isinstance(stored, str) and stored:
        return stored
    return f"{public_base_url.rstrip('/')}/{relative_path}"


def scan_index(data_dir: Path, public_base_url: str) -> list[GalleryEntry]:
    """Walk data_dir, parse every sidecar, return a list of entries.

    Tolerates missing optional fields, malformed JSON, and missing files.
    WebP files without a matching sidecar are silently skipped.
    """
    entries: list[GalleryEntry] = []
    if not data_dir.exists():
        return entries

    for sidecar_path in data_dir.rglob("*.json"):
        if not sidecar_path.is_file():
            continue
        # Relative path to data_dir (POSIX separators for URL & matching)
        try:
            rel = sidecar_path.relative_to(data_dir)
        except ValueError:
            continue

        rel_parts = rel.parts
        if len(rel_parts) < 2:
            # Must live under `<brand>/<name>.json`
            continue
        brand = rel_parts[0]

        try:
            raw = sidecar_path.read_text()
            sidecar = json.loads(raw)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Skipping unreadable sidecar %s: %s", sidecar_path, e)
            continue
        if not isinstance(sidecar, dict):
            continue

        # Expected companion WebP for this sidecar
        webp_path = sidecar_path.with_suffix(".webp")
        webp_rel = rel.with_suffix(".webp").as_posix()

        prompt = str(sidecar.get("prompt", ""))
        model = str(sidecar.get("model", ""))
        cost_estimate = str(sidecar.get("cost_estimate", ""))
        width, height = _parse_dimensions(sidecar.get("dimensions"))
        platform = sidecar.get("platform")
        if platform is not None and not isinstance(platform, str):
            platform = str(platform)

        file_size = sidecar.get("file_size_bytes") or sidecar.get("file_size")
        try:
            file_size_int = int(file_size) if file_size is not None else 0
        except (TypeError, ValueError):
            file_size_int = 0
        if not file_size_int and webp_path.exists():
            try:
                file_size_int = webp_path.stat().st_size
            except OSError:
                file_size_int = 0

        created_at = _parse_created_at(
            sidecar.get("generated_at") or sidecar.get("created_at"),
            sidecar_path,
        )

        hosted_url = _derive_hosted_url(sidecar, webp_rel, public_base_url)

        if width is None or height is None:
            # Minimal requirement: we need dims to display. Try the filename: "...-WxH.webp".
            stem = webp_path.stem
            if "-" in stem:
                last = stem.rsplit("-", 1)[-1]
                w2, h2 = _parse_dimensions(last)
                if w2 and h2:
                    width, height = w2, h2
        if width is None or height is None:
            width = width or 0
            height = height or 0

        entries.append(
            GalleryEntry(
                path=webp_rel,
                hosted_url=hosted_url,
                brand=brand,
                prompt=prompt,
                prompt_lower=prompt.lower(),
                model=model,
                cost_estimate=cost_estimate,
                width=int(width),
                height=int(height),
                platform=platform,
                file_size=file_size_int,
                created_at=created_at,
            )
        )

    return entries


_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500

_SORT_KEYS = {
    "created_desc": (lambda e: e.created_at, True),
    "created_asc": (lambda e: e.created_at, False),
    "cost_desc": (lambda e: _cost_to_float(e.cost_estimate), True),
    "size_desc": (lambda e: e.file_size, True),
}


def _cost_to_float(raw: str) -> float:
    """Extract a float from strings like '$0.03', '~$0.01/image', '0.05'."""
    if not raw:
        return 0.0
    buf = []
    seen_dot = False
    for ch in raw:
        if ch.isdigit():
            buf.append(ch)
        elif ch == "." and not seen_dot:
            buf.append(ch)
            seen_dot = True
        elif buf:
            break
    try:
        return float("".join(buf)) if buf else 0.0
    except ValueError:
        return 0.0


def _parse_iso_date(value: str) -> datetime | None:
    """Parse an ISO-8601 date or datetime; return None if invalid."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class GalleryIndex:
    """Holds the list + path-dict view of the index."""

    data_dir: Path
    public_base_url: str
    entries: list[GalleryEntry] = field(default_factory=list)
    by_path: dict[str, GalleryEntry] = field(default_factory=dict)

    def refresh(self) -> int:
        """Synchronously re-walk sidecars; return new total."""
        t0 = time.monotonic()
        new_entries = scan_index(self.data_dir, self.public_base_url)
        self.entries = new_entries
        self.by_path = {e.path: e for e in new_entries}
        logger.info(
            "Gallery reindex complete: %d entries in %.1f ms",
            len(new_entries),
            (time.monotonic() - t0) * 1000,
        )
        return len(new_entries)

    def total(self) -> int:
        return len(self.entries)

    def get(self, path: str) -> GalleryEntry | None:
        return self.by_path.get(path)

    def filter_and_sort(
        self,
        *,
        brand: list[str] | None = None,
        platform: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        q: str | None = None,
        min_width: int | None = None,
        min_height: int | None = None,
        sort: str = "created_desc",
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[int, list[GalleryEntry]]:
        """Return (total_after_filter, paged_slice)."""
        items: list[GalleryEntry] = list(self.entries)

        if brand:
            brand_set = {b for b in brand if b}
            if brand_set:
                items = [e for e in items if e.brand in brand_set]
        if platform:
            items = [e for e in items if e.platform == platform]
        if q:
            needle = q.lower()
            items = [e for e in items if needle in e.prompt_lower]
        if min_width is not None:
            items = [e for e in items if e.width >= min_width]
        if min_height is not None:
            items = [e for e in items if e.height >= min_height]
        if date_from:
            dt_from = _parse_iso_date(date_from)
            if dt_from is not None:
                items = [e for e in items if _aware(e.created_at) >= dt_from]
        if date_to:
            dt_to = _parse_iso_date(date_to)
            if dt_to is not None:
                # Inclusive upper bound: if only a date was given, treat as end-of-day UTC.
                if len(date_to) <= 10:
                    dt_to = dt_to.replace(hour=23, minute=59, second=59, microsecond=999999)
                items = [e for e in items if _aware(e.created_at) <= dt_to]

        key_fn, reverse = _SORT_KEYS.get(sort, _SORT_KEYS["created_desc"])
        items.sort(key=key_fn, reverse=reverse)

        total = len(items)
        if limit > _MAX_LIMIT:
            limit = _MAX_LIMIT
        if limit < 0:
            limit = 0
        if offset < 0:
            offset = 0
        paged = items[offset : offset + limit]
        return total, paged


def _aware(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (assume UTC if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _reindex_loop(index: GalleryIndex, interval_s: int) -> None:
    """Background refresh loop; logs INFO on success, WARN on failure."""
    interval = max(1, int(interval_s))
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        try:
            # Refresh is quick; run on the event-loop thread.
            index.refresh()
        except asyncio.CancelledError:
            return
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Gallery background reindex failed: %s", e)
