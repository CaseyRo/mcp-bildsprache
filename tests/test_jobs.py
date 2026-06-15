"""Unit tests for the async job registry + detached runner (CDI-1266).

Covers the in-process JobRegistry lifecycle (pending → done/error), the long-poll
wait, the eviction bound, and — the CRITICAL part — that spawn_detached keeps a
render running after the dispatching coroutine returns (it is NOT bound to the
caller's cancellation scope and is not garbage-collected mid-flight).
"""

import asyncio

import pytest

from mcp_bildsprache import jobs


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    """Give each test its own registry so job ids don't leak between tests."""
    reg = jobs.JobRegistry()
    monkeypatch.setattr(jobs, "_REGISTRY", reg)
    yield reg


class TestJobRegistry:
    def test_create_then_get_is_pending(self, _fresh_registry):
        rec = _fresh_registry.create("j1", model="gpt-image-2", brand="casey", dimensions="512x512")
        assert rec.status == "pending"
        got = _fresh_registry.get("j1")
        assert got is rec
        assert got.model == "gpt-image-2"
        assert got.brand == "casey"

    def test_mark_done_spreads_result(self, _fresh_registry):
        _fresh_registry.create("j1", model="m", brand="casey", dimensions="1x1")
        _fresh_registry.mark_done("j1", {"hosted_url": "https://x/y.webp", "model": "gpt-image-2"})
        out = _fresh_registry.get("j1").to_status_dict()
        assert out["status"] == "done"
        assert out["job_id"] == "j1"
        assert out["hosted_url"] == "https://x/y.webp"
        # Real provider model overrides the dispatch-time guess.
        assert out["model"] == "gpt-image-2"
        assert "latency_ms" in out

    def test_mark_error_sets_status_and_message(self, _fresh_registry):
        _fresh_registry.create("j1")
        _fresh_registry.mark_error("j1", error="boom", error_category="RuntimeError")
        out = _fresh_registry.get("j1").to_status_dict()
        assert out["status"] == "error"
        assert out["error"] == "boom"
        assert out["error_category"] == "RuntimeError"

    def test_get_unknown_is_none(self, _fresh_registry):
        assert _fresh_registry.get("nope") is None

    @pytest.mark.anyio
    async def test_wait_for_returns_immediately_when_done(self, _fresh_registry):
        _fresh_registry.create("j1")
        _fresh_registry.mark_done("j1", {"hosted_url": "u"})
        rec = await _fresh_registry.wait_for("j1", timeout=5)
        assert rec is not None and rec.status == "done"

    @pytest.mark.anyio
    async def test_wait_for_times_out_while_pending(self, _fresh_registry):
        _fresh_registry.create("j1")
        rec = await _fresh_registry.wait_for("j1", timeout=0.05)
        # Still pending after the short wait — caller polls again.
        assert rec is not None and rec.status == "pending"

    @pytest.mark.anyio
    async def test_wait_for_wakes_on_completion(self, _fresh_registry):
        _fresh_registry.create("j1")

        async def _finish_soon():
            await asyncio.sleep(0.02)
            _fresh_registry.mark_done("j1", {"hosted_url": "u"})

        task = asyncio.create_task(_finish_soon())
        rec = await _fresh_registry.wait_for("j1", timeout=5)
        await task
        assert rec is not None and rec.status == "done"

    @pytest.mark.anyio
    async def test_wait_for_unknown_returns_none(self, _fresh_registry):
        assert await _fresh_registry.wait_for("nope", timeout=0) is None

    def test_eviction_keeps_pending_records(self):
        reg = jobs.JobRegistry(max_records=2)
        reg.create("a")
        reg.mark_done("a", {"hosted_url": "u"})
        reg.create("b")  # pending
        reg.create("c")  # over cap → evicts the only non-pending one ('a')
        assert reg.get("a") is None
        assert reg.get("b") is not None  # pending never evicted
        assert reg.get("c") is not None


class TestSpawnDetached:
    @pytest.mark.anyio
    async def test_runs_to_completion_after_dispatcher_returns(self):
        """CRITICAL: the detached task keeps running after the coroutine that
        spawned it has returned — it is NOT tied to the caller's scope."""
        done = asyncio.Event()
        result_box = {}

        async def _render():
            await asyncio.sleep(0.03)
            result_box["ran"] = True
            done.set()

        async def _dispatcher():
            # Spawn and return WITHOUT awaiting the render.
            jobs.spawn_detached(_render, name="t")
            return "dispatched"

        assert await _dispatcher() == "dispatched"
        # The render had not finished when the dispatcher returned...
        assert "ran" not in result_box
        # ...but it completes on its own afterward.
        await asyncio.wait_for(done.wait(), timeout=2)
        assert result_box["ran"] is True

    @pytest.mark.anyio
    async def test_survives_caller_cancellation(self):
        """A detached render survives cancellation of the task that spawned it —
        it is owned by the module registry, not the request's cancellation scope."""
        done = asyncio.Event()

        async def _render():
            await asyncio.sleep(0.05)
            done.set()

        async def _request_scope():
            jobs.spawn_detached(_render, name="t")
            # Simulate the request task being cancelled mid-flight (portal timeout).
            await asyncio.sleep(0.01)
            raise asyncio.CancelledError()

        task = asyncio.create_task(_request_scope())
        with pytest.raises(asyncio.CancelledError):
            await task

        # The render keeps going despite the request scope being torn down.
        await asyncio.wait_for(done.wait(), timeout=2)
        assert done.is_set()

    @pytest.mark.anyio
    async def test_strong_reference_held_until_done(self):
        """The task is tracked (strong ref) while in flight and discarded after."""
        gate = asyncio.Event()

        async def _render():
            await gate.wait()

        task = jobs.spawn_detached(_render, name="t")
        assert jobs.pending_task_count() >= 1
        gate.set()
        await task
        # done-callback runs on the next loop tick; give it a chance.
        await asyncio.sleep(0)
        assert task not in jobs._BACKGROUND_TASKS

    @pytest.mark.anyio
    async def test_uncaught_exception_is_retrieved_not_warned_to_caller(self):
        """An exception escaping the render is retrieved by the done-callback
        (no 'Task exception was never retrieved') and never propagates here."""

        async def _render():
            raise ValueError("kaboom")

        task = jobs.spawn_detached(_render, name="t")
        # Awaiting it surfaces the exception to us, but the done-callback also
        # retrieves it so the loop never logs the unretrieved-exception warning.
        with pytest.raises(ValueError, match="kaboom"):
            await task
        await asyncio.sleep(0)
        assert task not in jobs._BACKGROUND_TASKS
