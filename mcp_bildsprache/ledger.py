"""Append-only generation outcome ledger (CDI-1264).

The gallery sidecars record only SUCCESSES; failures previously lived only in
ephemeral container logs and vanished on restart. This module persists one
record per generation ATTEMPT — success and failure alike — to a durable,
append-only JSONL file on the same volume as the gallery, so the
attempts-vs-model-vs-outcome picture is measurable over time and across
restarts.

Design choices:

* **JSONL** (one JSON object per line). Append-only, restart-safe, and free of
  read-modify-write races: each writer opens the file in append mode and emits
  a single ``write()`` of one newline-terminated line. POSIX guarantees small
  appends to ``O_APPEND`` files are atomic, so concurrent generations never
  interleave partial lines.
* **Best-effort writes** (``append_record``): wrapped in try/except and logged
  at debug on failure. A ledger write MUST NEVER break a generation — the
  artifact and the response take priority over telemetry.
* **Streaming/tail reads** (``read_records``): the stats reader tails the file
  from the end and stops once it has collected enough lines / passed the time
  window, so an unbounded ledger is never loaded whole into memory.

Outcome vocabulary (the ``outcome`` field):

* ``success``               — image generated + saved. ``delivery`` distinguishes
                              whether the caller actually received the response
                              (``delivered``) or the streamable-HTTP session was
                              torn down mid/after render (``teardown_closed_stream``).
* ``provider_error``        — provider 4xx/5xx or any provider-call exception.
* ``timeout``               — render exceeded the allotted window.
* ``teardown_closed_stream`` — reserved standalone outcome; in practice the
                              torn-down-but-successful case is recorded as
                              ``success`` + ``delivery="teardown_closed_stream"``
                              (see the success-vs-teardown convention below).
* ``other``                 — anything else.

Success-vs-teardown convention (the truthful representation we picked):
a closed/broken stream during *progress-notification delivery* does NOT mean the
generation failed — the image is generated, saved, and indexed. So we record the
GENERATION outcome as ``success`` and capture the delivery fact in a separate
``delivery`` field (``delivered`` | ``teardown_closed_stream``). This keeps
success% honest (the render really did succeed and was billed) while still making
"the caller never got the URL" measurable. ``outcome="teardown_closed_stream"``
is reserved for the (currently unreached) case where delivery failed *and* we
cannot confirm the artifact was saved.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

Outcome = Literal[
    "success",
    "provider_error",
    "timeout",
    "teardown_closed_stream",
    "other",
]
Delivery = Literal["delivered", "teardown_closed_stream"]

# Hard ceiling on how many lines a single stats read will pull into memory,
# regardless of the caller's `limit`. Protects the process from an
# unbounded ledger; the tail read stops as soon as this many in-window
# records are collected (newest first).
_MAX_READ_LINES = 50_000

# Tail read chunk size (bytes read per backward step).
_TAIL_CHUNK = 64 * 1024


def _utc_now_iso() -> str:
    """UTC ISO-8601 with a trailing Z (matches the attribution timestamp shape)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_request_id() -> str:
    """Fresh correlation id for one generation attempt."""
    return uuid.uuid4().hex


def _truncate(message: str | None, limit: int = 500) -> str | None:
    """Truncate a (possibly multi-line) error message to ``limit`` chars."""
    if message is None:
        return None
    text = str(message).strip().replace("\n", " ")
    if len(text) > limit:
        return text[: limit - 1] + "…"  # ellipsis
    return text


def build_record(
    *,
    request_id: str,
    outcome: Outcome,
    model: str | None,
    provider: str | None,
    brand: str | None,
    width: int | None,
    height: int | None,
    requested_size: str | None = None,
    latency_ms: int | None = None,
    error_category: str | None = None,
    error_message: str | None = None,
    hosted_url: str | None = None,
    cost_estimate_eur: float | None = None,
    delivery: Delivery | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble one ledger record dict. Pure; does not touch disk.

    Optional fields are omitted (rather than emitted as null) so each line
    stays compact and a reader can treat missing == not-applicable.
    """
    record: dict[str, Any] = {
        "ts": _utc_now_iso(),
        "request_id": request_id,
        "outcome": outcome,
        "model": model,
        "provider": provider,
        "brand": brand,
    }
    # `requested_size` defaults to "WxH" when both dims are known so the
    # ticket's requested_size/dimensions pair is always present together.
    if requested_size is None and width and height:
        requested_size = f"{width}x{height}"
    if requested_size is not None:
        record["requested_size"] = requested_size
    if width is not None:
        record["width"] = width
    if height is not None:
        record["height"] = height
    if latency_ms is not None:
        record["latency_ms"] = latency_ms
    if error_category is not None:
        record["error_category"] = error_category
    msg = _truncate(error_message)
    if msg is not None:
        record["error_message"] = msg
    if hosted_url is not None:
        record["hosted_url"] = hosted_url
    if cost_estimate_eur is not None:
        record["cost_estimate_eur"] = cost_estimate_eur
    if delivery is not None:
        record["delivery"] = delivery
    if extra:
        record.update(extra)
    return record


def append_record(record: dict[str, Any], *, path: Path | None = None) -> bool:
    """Append one record to the JSONL ledger. Best-effort; never raises.

    Returns ``True`` on a successful write, ``False`` on any failure (logged at
    debug). The directory is created if missing. The whole line — including its
    trailing newline — is written in a single ``write()`` under ``O_APPEND`` so
    concurrent attempts cannot interleave partial lines.
    """
    from mcp_bildsprache.config import settings

    if not settings.ledger_enabled:
        return False

    target = path or settings.resolved_ledger_path
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        # Append mode + single write keeps multi-process appends atomic.
        with target.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return True
    except Exception:  # pragma: no cover — telemetry must never break generation
        logger.debug("ledger append failed for %s", target, exc_info=True)
        return False


def _iter_lines_reverse(path: Path, *, max_lines: int) -> list[str]:
    """Return up to ``max_lines`` trailing lines of ``path``, newest last.

    Tails the file backwards in chunks so a huge ledger is never read whole.
    The returned list preserves on-disk order (oldest first) for the tail
    window; callers that want newest-first reverse it themselves.
    """
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size == 0:
        return []

    collected: list[bytes] = []
    newline_count = 0
    try:
        with path.open("rb") as fh:
            pos = size
            buffer = b""
            while pos > 0 and newline_count <= max_lines:
                read_size = min(_TAIL_CHUNK, pos)
                pos -= read_size
                fh.seek(pos)
                chunk = fh.read(read_size)
                buffer = chunk + buffer
                newline_count = buffer.count(b"\n")
            collected = [buffer]
    except OSError:
        return []

    raw = b"".join(collected)
    text_lines = raw.decode("utf-8", errors="replace").splitlines()
    if len(text_lines) > max_lines:
        text_lines = text_lines[-max_lines:]
    return text_lines


def read_records(
    *,
    path: Path | None = None,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Read ledger records newest-first, windowed by ``since`` and ``limit``.

    Streams the file from the tail so only the needed suffix is materialised.
    ``limit`` is clamped to ``_MAX_READ_LINES``. Malformed lines are skipped
    (logged at debug). Never raises on a missing/unreadable file — returns ``[]``.
    """
    target = path or _default_path()
    hard_cap = _MAX_READ_LINES if limit is None else min(int(limit), _MAX_READ_LINES)
    # Pull a generous tail window: filtering by `since` may drop many of the
    # most-recent lines if they fall outside the window, but the tail is the
    # cheapest place to start. We read up to the hard cap of *candidate* lines.
    raw_lines = _iter_lines_reverse(target, max_lines=_MAX_READ_LINES)

    out: list[dict[str, Any]] = []
    for line in reversed(raw_lines):  # newest first
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("ledger: skipping malformed line", exc_info=True)
            continue
        if not isinstance(rec, dict):
            continue
        if since is not None:
            ts = _parse_ts(rec.get("ts"))
            if ts is None or ts < since:
                continue
        out.append(rec)
        if len(out) >= hard_cap:
            break
    return out


def find_by_request_id(
    request_id: str, *, path: Path | None = None
) -> dict[str, Any] | None:
    """Return the most recent ledger record whose ``request_id`` matches, or None.

    Durable cross-restart fallback for ``get_image_result`` (CDI-1266): when a
    ``job_id`` (== the ledger ``request_id`` for the same attempt) is no longer in
    the in-process job registry — container restarted mid/after render, or a
    different worker handled it — the result is still recoverable here. Scans the
    ledger tail newest-first and stops at the first matching line, so a hit on a
    recent attempt is cheap. Never raises on a missing/unreadable file.
    """
    if not request_id:
        return None
    target = path or _default_path()
    raw_lines = _iter_lines_reverse(target, max_lines=_MAX_READ_LINES)
    for line in reversed(raw_lines):  # newest first
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("request_id") == request_id:
            return rec
    return None


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _default_path() -> Path:
    from mcp_bildsprache.config import settings

    return settings.resolved_ledger_path


@dataclass
class ModelStat:
    """Aggregate counts for one model over the requested window."""

    model: str
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if self.attempts == 0:
            return 0.0
        return round(self.successes / self.attempts, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": self.success_rate,
            "success_pct": round(self.success_rate * 100, 2),
            "outcomes": dict(self.outcomes),
        }


def compute_stats(
    *,
    path: Path | None = None,
    since: datetime | None = None,
    days: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Aggregate ledger records into per-model attempt/success/failure counts.

    Window resolution: an explicit ``since`` wins; otherwise ``days`` (now -
    ``days``); otherwise the whole (capped) tail. ``limit`` caps how many
    records are scanned (newest first), so even an enormous ledger answers in
    bounded time/memory.

    A record counts as a success when ``outcome == "success"`` — note that a
    torn-down-delivery success (``delivery == "teardown_closed_stream"``) is
    still a generation success here; the delivery breakdown is surfaced
    separately so callers can see how many succeeded-but-undelivered.
    """
    if since is None and days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=days)

    records = read_records(path=path, since=since, limit=limit)

    # Deterministic, stable ordering of models (insertion order = first seen).
    by_model: OrderedDict[str, ModelStat] = OrderedDict()
    total_attempts = 0
    total_successes = 0
    total_failures = 0
    delivered = 0
    torn_down = 0

    for rec in records:
        model = rec.get("model") or "unknown"
        outcome = rec.get("outcome") or "other"
        stat = by_model.get(model)
        if stat is None:
            stat = ModelStat(model=model)
            by_model[model] = stat
        stat.attempts += 1
        stat.outcomes[outcome] = stat.outcomes.get(outcome, 0) + 1
        total_attempts += 1
        if outcome == "success":
            stat.successes += 1
            total_successes += 1
            if rec.get("delivery") == "teardown_closed_stream":
                torn_down += 1
            else:
                delivered += 1
        else:
            stat.failures += 1
            total_failures += 1

    overall_rate = (
        round(total_successes / total_attempts, 4) if total_attempts else 0.0
    )

    return {
        "window": {
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else None,
            "days": days,
            "limit": limit,
        },
        "totals": {
            "attempts": total_attempts,
            "successes": total_successes,
            "failures": total_failures,
            "success_rate": overall_rate,
            "success_pct": round(overall_rate * 100, 2),
            "delivered": delivered,
            "teardown_closed_stream": torn_down,
        },
        "by_model": [s.to_dict() for s in by_model.values()],
    }


# ---------------------------------------------------------------------------
# Backfill (one-shot, idempotent) — seed historical successes from gallery
# sidecars so the ledger isn't empty on day one. NOT run on startup; invoke
# via `python -m mcp_bildsprache --backfill` (see __main__) or call directly.
# ---------------------------------------------------------------------------


_BACKFILL_SOURCE = "gallery-backfill"


def backfill_from_gallery(
    *,
    data_dir: Path | None = None,
    ledger_path: Path | None = None,
) -> dict[str, int]:
    """Seed the ledger with one ``success`` record per existing gallery sidecar.

    Idempotent: each backfilled record carries ``source="gallery-backfill"`` and
    a ``request_id`` derived deterministically from the artifact's relative path
    (``backfill:<path>``). Already-present request_ids are skipped, so re-running
    never double-seeds. Returns ``{"scanned", "seeded", "skipped"}``.

    Reads the SAME sidecars the gallery indexes (``<data_dir>/**/*.json``) via
    the gallery scanner, so the schema interpretation stays in one place.
    """
    from mcp_bildsprache.config import settings
    from mcp_bildsprache.gallery.index import scan_index

    src_dir = data_dir or Path(settings.image_storage_path)
    target = ledger_path or settings.resolved_ledger_path

    # Existing backfill ids → skip set (idempotency guard).
    existing_ids: set[str] = set()
    for rec in read_records(path=target, limit=_MAX_READ_LINES):
        rid = rec.get("request_id")
        if isinstance(rid, str) and rid.startswith("backfill:"):
            existing_ids.add(rid)

    entries = scan_index(src_dir, settings.image_domain)
    scanned = len(entries)
    seeded = 0
    skipped = 0

    for entry in entries:
        request_id = f"backfill:{entry.path}"
        if request_id in existing_ids:
            skipped += 1
            continue
        provider = _provider_from_model(entry.model)
        cost_eur = _eur_from_cost_string(entry.cost_estimate)
        record = build_record(
            request_id=request_id,
            outcome="success",
            model=entry.model or None,
            provider=provider,
            brand=entry.brand or None,
            width=entry.width or None,
            height=entry.height or None,
            requested_size=(
                f"{entry.width}x{entry.height}" if entry.width and entry.height else None
            ),
            hosted_url=entry.hosted_url,
            cost_estimate_eur=cost_eur,
            delivery="delivered",
            extra={
                "source": _BACKFILL_SOURCE,
                # Preserve the artifact's real generation time so the
                # backfilled record sits at the right point in the window.
                "ts": entry.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        if append_record(record, path=target):
            seeded += 1
            existing_ids.add(request_id)

    logger.info(
        "ledger backfill: scanned=%d seeded=%d skipped=%d (already present)",
        scanned,
        seeded,
        skipped,
    )
    return {"scanned": scanned, "seeded": seeded, "skipped": skipped}


def _provider_from_model(model: str) -> str | None:
    if not model:
        return None
    if model.startswith("gpt-image"):
        return "openai"
    if model.startswith("gemini"):
        return "gemini"
    if model.startswith("flux"):
        return "bfl"
    if model.startswith("recraft"):
        return "recraft"
    return None


def _eur_from_cost_string(raw: str) -> float | None:
    """Parse '€0.0490' / '$0.05' / '0.05' into a float; None when unparseable."""
    if not raw:
        return None
    buf: list[str] = []
    seen_dot = False
    for ch in raw:
        if ch.isdigit():
            buf.append(ch)
        elif ch == "." and not seen_dot:
            buf.append(ch)
            seen_dot = True
        elif buf:
            break
    if not buf:
        return None
    try:
        return float("".join(buf))
    except ValueError:
        return None
