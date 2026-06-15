"""In-process job registry + detached background runner for async image renders (CDI-1266).

Why this exists
---------------
Clients reach this server through a Cloudflare-MANAGED MCP portal with a hard
~60s upstream read timeout we cannot change. gpt-image-2 / Nano-Banana-Pro
renders take 50-80s, so a synchronous ``generate_image`` response is severed by
the portal (``-32001 Request timed out``) even though the render completes
server-side. The fix (CDI-1266): dispatch the render in the BACKGROUND, return a
fast job handle under the portal budget, and let the client poll for the result
with ``get_image_result``.

The critical risk
-----------------
In a FastMCP stateless_http server the work scheduled inside a tool handler runs
on the request's task. If we ``await`` the render inside the handler the portal
timeout tears the request (and its task) down mid-render. Worse, a naive
``asyncio.create_task`` inside the handler can still be tied to the request's
cancellation scope (anyio task groups cancel children when the scope exits), or
be garbage-collected before it finishes (CPython only holds a *weak* reference to
a bare task) — either way the render dies when the request ends.

This module detaches the render from the request scope:

* :func:`spawn_detached` schedules the coroutine on the *running event loop*
  (``loop.create_task``) — the loop outlives any single request — and stores a
  STRONG reference to the task in a module-global set so it is never GC'd
  mid-flight. A done-callback discards the reference and retrieves the result so
  Python never logs a "Task exception was never retrieved" warning.
* The render coroutine itself takes NO ``Context`` and writes no progress
  notifications, so it cannot be killed by a closed/torn-down stream.

The registry
------------
:class:`JobRegistry` maps ``job_id -> JobRecord`` (status pending|done|error +
the eventual result/error). ``job_id`` IS the CDI-1264 ledger ``request_id``, so
a result that has fallen out of the in-process registry (container restart, a
different worker) is still recoverable from the durable ledger by the same id —
see ``get_image_result``'s ledger fallback in ``server.py``.

Everything here is process-local and best-effort: a registry miss is never an
error, it just means "ask the ledger / list_recent_generations instead".
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

logger = logging.getLogger(__name__)

JobStatus = Literal["pending", "done", "error"]

# Strong references to in-flight detached tasks. CPython's event loop keeps only
# a WEAK reference to a task, so without this a background render can be
# garbage-collected mid-flight and silently cancelled. We add the task on spawn
# and discard it from a done-callback (see _spawn).
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


@dataclass
class JobRecord:
    """One background render's lifecycle + outcome, keyed by ``job_id``.

    ``job_id`` is the CDI-1264 ledger ``request_id`` for the same attempt, so the
    durable ledger is the cross-restart fallback for this in-memory record.
    """

    job_id: str
    status: JobStatus = "pending"
    model: str | None = None
    brand: str | None = None
    dimensions: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # On success: the full generate_image/generate_diagram result dict (so a poll
    # returns the same shape the synchronous path would have).
    result: dict[str, Any] | None = None
    # On failure: a human-readable error string + the exception class name.
    error: str | None = None
    error_category: str | None = None

    def latency_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at) * 1000)

    def to_status_dict(self) -> dict[str, Any]:
        """Project to the ``get_image_result`` response shape.

        On ``done`` the stored result dict is spread in (so ``hosted_url``,
        ``ai_attribution``, ``cost_estimate``, ... appear at the top level exactly
        as the synchronous path returns them) with ``job_id`` / ``status`` /
        ``latency_ms`` overlaid. On ``pending`` / ``error`` only the envelope
        fields are present.
        """
        out: dict[str, Any] = {}
        if self.status == "done" and self.result is not None:
            out.update(self.result)
        out["job_id"] = self.job_id
        out["status"] = self.status
        if self.model is not None:
            out.setdefault("model", self.model)
        if self.brand is not None:
            out.setdefault("brand_context", self.brand)
        if self.dimensions is not None:
            out.setdefault("dimensions", self.dimensions)
        latency = self.latency_ms()
        if latency is not None:
            out["latency_ms"] = latency
        if self.status == "error" and self.error is not None:
            out["error"] = self.error
            if self.error_category is not None:
                out["error_category"] = self.error_category
        return out


class JobRegistry:
    """Process-local job registry. Thread/loop-affine; mutated only from the loop.

    A bound on the number of retained records keeps memory flat over a
    long-running process: once ``max_records`` is exceeded the oldest entries are
    evicted (the durable ledger remains the long-term record either way).
    """

    def __init__(self, *, max_records: int = 2048) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._max_records = max_records
        # Per-job completion event so a long-poll can wait efficiently instead of
        # spinning. Created lazily on first wait.
        self._events: dict[str, asyncio.Event] = {}

    def create(
        self,
        job_id: str,
        *,
        model: str | None = None,
        brand: str | None = None,
        dimensions: str | None = None,
    ) -> JobRecord:
        record = JobRecord(
            job_id=job_id, model=model, brand=brand, dimensions=dimensions
        )
        self._jobs[job_id] = record
        self._evict_if_needed()
        return record

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def mark_done(self, job_id: str, result: dict[str, Any]) -> None:
        record = self._jobs.get(job_id)
        if record is None:
            return
        record.status = "done"
        record.result = result
        record.finished_at = time.time()
        # Prefer the real provider model id from the result when present.
        model = result.get("model")
        if isinstance(model, str) and model:
            record.model = model
        self._fire_event(job_id)

    def mark_error(
        self, job_id: str, *, error: str, error_category: str | None = None
    ) -> None:
        record = self._jobs.get(job_id)
        if record is None:
            return
        record.status = "error"
        record.error = error
        record.error_category = error_category
        record.finished_at = time.time()
        self._fire_event(job_id)

    async def wait_for(self, job_id: str, timeout: float) -> JobRecord | None:
        """Wait up to ``timeout`` seconds for ``job_id`` to leave ``pending``.

        Returns the (possibly still-pending) record, or ``None`` if the id is
        unknown. A ``timeout <= 0`` returns the current record immediately
        without awaiting (single-shot poll).
        """
        record = self._jobs.get(job_id)
        if record is None:
            return None
        if record.status != "pending" or timeout <= 0:
            return record
        event = self._events.setdefault(job_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            pass
        return self._jobs.get(job_id)

    def _fire_event(self, job_id: str) -> None:
        event = self._events.get(job_id)
        if event is not None:
            event.set()

    def _evict_if_needed(self) -> None:
        # Evict oldest entries (insertion order) once over the cap. Only evict
        # records that are no longer pending so an in-flight render's record is
        # never dropped out from under its background task.
        while len(self._jobs) > self._max_records:
            for jid, rec in self._jobs.items():
                if rec.status != "pending":
                    self._jobs.pop(jid, None)
                    self._events.pop(jid, None)
                    break
            else:
                # All remaining are pending — stop evicting to avoid losing
                # in-flight work; the cap is soft under heavy concurrency.
                break


# Module-global registry instance. Process-local; the ledger is the durable
# cross-restart store.
_REGISTRY = JobRegistry()


def get_registry() -> JobRegistry:
    return _REGISTRY


def spawn_detached(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    name: str | None = None,
) -> asyncio.Task[Any]:
    """Run ``coro_factory()`` on the running loop, detached from the caller's scope.

    Returns the created ``asyncio.Task``. The task is held by a strong module-level
    reference (so it can't be GC'd mid-flight) and its exception is retrieved in a
    done-callback (so Python never logs "Task exception was never retrieved").

    The caller (the dispatching tool handler) MUST NOT ``await`` the returned task
    as part of its own completion — the whole point is that the render outlives the
    request. The handler MAY ``asyncio.wait({task}, timeout=...)`` to optionally
    inline-deliver a fast result, but the task continues regardless of whether the
    wait times out or the request is torn down.
    """
    loop = asyncio.get_running_loop()
    task = loop.create_task(coro_factory(), name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_on_task_done)
    return task


def _on_task_done(task: asyncio.Task[Any]) -> None:
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        logger.debug("background render task cancelled: %s", task.get_name())
        return
    exc = task.exception()
    if exc is not None:
        # The render coroutine is expected to catch its own exceptions and record
        # them into the registry/ledger; reaching here means something escaped.
        # Log it (retrieving it suppresses the asyncio warning) but never raise.
        logger.warning(
            "background render task raised uncaught: %s", exc, exc_info=exc
        )


def pending_task_count() -> int:
    """Number of in-flight detached background tasks (for tests/observability)."""
    return len(_BACKGROUND_TASKS)
