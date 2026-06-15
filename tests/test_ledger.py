"""Unit tests for the generation outcome ledger (CDI-1264).

Covers the durable JSONL ledger module (`mcp_bildsprache/ledger.py`):
append/read round-trip + restart persistence, windowed per-model stats,
best-effort writes that never raise, the success-vs-teardown convention,
and idempotent gallery backfill. The server-side wiring (one ledger line per
attempt on the success AND failure paths, plus the `generation_stats` tool) is
exercised in `test_integration.py`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp_bildsprache import ledger


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestAppendAndRead:
    def test_append_then_read_roundtrip(self, tmp_path: Path):
        ledger_path = tmp_path / "_ledger" / "generations.jsonl"
        rec = ledger.build_record(
            request_id="r1",
            outcome="success",
            model="gpt-image-2",
            provider="openai",
            brand="casey",
            width=1200,
            height=1200,
            latency_ms=4200,
            hosted_url="https://img.cdit-works.de/casey/a-1200x1200.webp",
            cost_estimate_eur=0.049,
            delivery="delivered",
        )
        assert ledger.append_record(rec, path=ledger_path) is True

        lines = _read_lines(ledger_path)
        assert len(lines) == 1
        got = lines[0]
        assert got["request_id"] == "r1"
        assert got["outcome"] == "success"
        assert got["model"] == "gpt-image-2"
        assert got["requested_size"] == "1200x1200"
        assert got["delivery"] == "delivered"
        # ts is UTC ISO8601 with Z.
        assert got["ts"].endswith("Z")

    def test_append_is_append_only_across_calls(self, tmp_path: Path):
        """Persistence across 'restarts' = a fresh append to the same file."""
        ledger_path = tmp_path / "led.jsonl"
        for i in range(3):
            ledger.append_record(
                ledger.build_record(
                    request_id=f"r{i}",
                    outcome="success",
                    model="gpt-image-2",
                    provider="openai",
                    brand="casey",
                    width=1024,
                    height=1024,
                ),
                path=ledger_path,
            )
        # Simulate a process restart: read back from a cold path.
        recs = ledger.read_records(path=ledger_path)
        assert [r["request_id"] for r in recs] == ["r2", "r1", "r0"]  # newest first

    def test_read_missing_file_returns_empty(self, tmp_path: Path):
        assert ledger.read_records(path=tmp_path / "nope.jsonl") == []

    def test_read_skips_malformed_lines(self, tmp_path: Path):
        ledger_path = tmp_path / "led.jsonl"
        ledger_path.write_text(
            '{"ts":"2026-06-01T00:00:00Z","outcome":"success","model":"m","request_id":"ok"}\n'
            "this is not json\n"
            '{"ts":"2026-06-02T00:00:00Z","outcome":"timeout","model":"m","request_id":"ok2"}\n'
        )
        recs = ledger.read_records(path=ledger_path)
        assert {r["request_id"] for r in recs} == {"ok", "ok2"}

    def test_long_error_message_is_truncated(self, tmp_path: Path):
        ledger_path = tmp_path / "led.jsonl"
        long_msg = "x" * 2000
        ledger.append_record(
            ledger.build_record(
                request_id="r",
                outcome="provider_error",
                model="gpt-image-2",
                provider="openai",
                brand="casey",
                width=1024,
                height=1024,
                error_category="HTTPStatusError",
                error_message=long_msg,
            ),
            path=ledger_path,
        )
        got = _read_lines(ledger_path)[0]
        assert len(got["error_message"]) <= 500


class TestFindByRequestId:
    """The CDI-1266 durable fallback lookup used by get_image_result."""

    def _seed(self, path: Path, request_id: str, **kw):
        rec = ledger.build_record(
            request_id=request_id,
            outcome=kw.pop("outcome", "success"),
            model=kw.pop("model", "gpt-image-2"),
            provider=kw.pop("provider", "openai"),
            brand=kw.pop("brand", "casey"),
            width=kw.pop("width", 1024),
            height=kw.pop("height", 1024),
            **kw,
        )
        ledger.append_record(rec, path=path)

    def test_returns_matching_record(self, tmp_path: Path):
        led = tmp_path / "led.jsonl"
        self._seed(led, "a", hosted_url="https://x/a.webp")
        self._seed(led, "b", hosted_url="https://x/b.webp")
        got = ledger.find_by_request_id("b", path=led)
        assert got is not None
        assert got["request_id"] == "b"
        assert got["hosted_url"] == "https://x/b.webp"

    def test_returns_newest_when_duplicate_ids(self, tmp_path: Path):
        led = tmp_path / "led.jsonl"
        self._seed(led, "dup", hosted_url="https://x/old.webp")
        self._seed(led, "dup", hosted_url="https://x/new.webp")
        got = ledger.find_by_request_id("dup", path=led)
        assert got is not None
        assert got["hosted_url"] == "https://x/new.webp"

    def test_unknown_id_returns_none(self, tmp_path: Path):
        led = tmp_path / "led.jsonl"
        self._seed(led, "a")
        assert ledger.find_by_request_id("missing", path=led) is None

    def test_missing_file_returns_none(self, tmp_path: Path):
        assert ledger.find_by_request_id("a", path=tmp_path / "nope.jsonl") is None

    def test_empty_request_id_returns_none(self, tmp_path: Path):
        led = tmp_path / "led.jsonl"
        self._seed(led, "a")
        assert ledger.find_by_request_id("", path=led) is None


class TestBestEffortWrites:
    def test_append_never_raises_on_bad_path(self, tmp_path: Path):
        """A write failure must return False, not raise (acceptance: ledger
        write failures never break generation)."""
        # Point the ledger at a path whose parent is a *file*, so mkdir fails.
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file, not a dir")
        bad_path = blocker / "sub" / "led.jsonl"
        assert ledger.append_record(
            ledger.build_record(
                request_id="r",
                outcome="success",
                model="m",
                provider="openai",
                brand="casey",
                width=1,
                height=1,
            ),
            path=bad_path,
        ) is False

    def test_disabled_ledger_skips_write(self, tmp_path: Path, monkeypatch):
        from mcp_bildsprache.config import settings

        monkeypatch.setattr(settings, "ledger_enabled", False)
        ledger_path = tmp_path / "led.jsonl"
        assert ledger.append_record(
            ledger.build_record(
                request_id="r",
                outcome="success",
                model="m",
                provider="openai",
                brand="casey",
                width=1,
                height=1,
            ),
            path=ledger_path,
        ) is False
        assert not ledger_path.exists()


class TestComputeStats:
    @staticmethod
    def _seed(path: Path, records: list[dict]) -> None:
        for r in records:
            ledger.append_record(r, path=path)

    def test_per_model_attempts_success_fail_pct(self, tmp_path: Path):
        ledger_path = tmp_path / "led.jsonl"
        now = datetime.now(timezone.utc)
        recs = []
        # gpt-image-2: 3 attempts, 2 success, 1 provider_error -> 66.67%
        for i, outcome in enumerate(["success", "success", "provider_error"]):
            r = ledger.build_record(
                request_id=f"o{i}",
                outcome=outcome,
                model="gpt-image-2",
                provider="openai",
                brand="casey",
                width=1024,
                height=1024,
            )
            r["ts"] = (now - timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            recs.append(r)
        # gemini: 1 attempt, 1 timeout -> 0%
        g = ledger.build_record(
            request_id="g0",
            outcome="timeout",
            model="gemini-3.1-flash-image-preview",
            provider="gemini",
            brand="casey",
            width=1600,
            height=900,
        )
        g["ts"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        recs.append(g)
        self._seed(ledger_path, recs)

        stats = ledger.compute_stats(path=ledger_path, days=7)

        assert stats["totals"]["attempts"] == 4
        assert stats["totals"]["successes"] == 2
        assert stats["totals"]["failures"] == 2
        assert stats["totals"]["success_pct"] == 50.0

        by_model = {m["model"]: m for m in stats["by_model"]}
        gpt = by_model["gpt-image-2"]
        assert gpt["attempts"] == 3
        assert gpt["successes"] == 2
        assert gpt["failures"] == 1
        assert gpt["success_pct"] == 66.67
        assert gpt["outcomes"] == {"success": 2, "provider_error": 1}

        gem = by_model["gemini-3.1-flash-image-preview"]
        assert gem["attempts"] == 1
        assert gem["success_pct"] == 0.0
        assert gem["outcomes"] == {"timeout": 1}

    def test_window_excludes_old_records(self, tmp_path: Path):
        ledger_path = tmp_path / "led.jsonl"
        now = datetime.now(timezone.utc)
        old = ledger.build_record(
            request_id="old", outcome="success", model="gpt-image-2",
            provider="openai", brand="casey", width=1, height=1,
        )
        old["ts"] = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent = ledger.build_record(
            request_id="recent", outcome="success", model="gpt-image-2",
            provider="openai", brand="casey", width=1, height=1,
        )
        recent["ts"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._seed(ledger_path, [old, recent])

        stats = ledger.compute_stats(path=ledger_path, days=30)
        assert stats["totals"]["attempts"] == 1  # the 90-day-old one is excluded

        all_time = ledger.compute_stats(path=ledger_path, days=None)
        assert all_time["totals"]["attempts"] == 2

    def test_empty_ledger_clean_zeros_not_error(self, tmp_path: Path):
        stats = ledger.compute_stats(path=tmp_path / "absent.jsonl", days=30)
        assert "error" not in stats
        assert stats["totals"]["attempts"] == 0
        assert stats["totals"]["success_pct"] == 0.0
        assert stats["by_model"] == []

    def test_teardown_delivery_counts_as_success_but_tracked(self, tmp_path: Path):
        """Success-vs-teardown convention: a torn-down-delivery render is still a
        generation success, but the succeeded-but-undelivered count is tracked."""
        ledger_path = tmp_path / "led.jsonl"
        self._seed(
            ledger_path,
            [
                ledger.build_record(
                    request_id="ok", outcome="success", model="gpt-image-2",
                    provider="openai", brand="casey", width=1, height=1,
                    delivery="delivered",
                ),
                ledger.build_record(
                    request_id="torn", outcome="success", model="gpt-image-2",
                    provider="openai", brand="casey", width=1, height=1,
                    delivery="teardown_closed_stream",
                ),
            ],
        )
        stats = ledger.compute_stats(path=ledger_path, days=None)
        assert stats["totals"]["successes"] == 2  # both are successes
        assert stats["totals"]["success_pct"] == 100.0
        assert stats["totals"]["delivered"] == 1
        assert stats["totals"]["teardown_closed_stream"] == 1


class TestBackfill:
    @staticmethod
    def _seed_sidecar(root: Path, brand: str, slug: str, *, w=1200, h=1200) -> None:
        d = root / brand
        d.mkdir(parents=True, exist_ok=True)
        stem = f"{slug}-{w}x{h}"
        (d / f"{stem}.webp").write_bytes(b"fakewebp")
        (d / f"{stem}.json").write_text(
            json.dumps(
                {
                    "prompt": slug,
                    "model": "gpt-image-2",
                    "cost_estimate": "€0.0490",
                    "dimensions": f"{w}x{h}",
                    "generated_at": "2026-05-01T12:00:00+00:00",
                    "hosted_url": f"https://img.cdit-works.de/{brand}/{stem}.webp",
                }
            )
        )

    def test_backfill_seeds_successes_from_sidecars(self, tmp_path: Path, monkeypatch):
        from mcp_bildsprache.config import settings

        data_dir = tmp_path / "images"
        ledger_path = tmp_path / "_ledger" / "generations.jsonl"
        self._seed_sidecar(data_dir, "casey", "alpha")
        self._seed_sidecar(data_dir, "yorizon", "beta")

        monkeypatch.setattr(settings, "image_storage_path", str(data_dir))

        summary = ledger.backfill_from_gallery(data_dir=data_dir, ledger_path=ledger_path)
        assert summary["scanned"] == 2
        assert summary["seeded"] == 2
        assert summary["skipped"] == 0

        recs = ledger.read_records(path=ledger_path)
        assert len(recs) == 2
        assert all(r["outcome"] == "success" for r in recs)
        assert all(r["source"] == "gallery-backfill" for r in recs)
        assert all(r["request_id"].startswith("backfill:") for r in recs)
        # Preserves the artifact's real generation time.
        assert all(r["ts"].startswith("2026-05-01") for r in recs)

    def test_backfill_is_idempotent(self, tmp_path: Path, monkeypatch):
        from mcp_bildsprache.config import settings

        data_dir = tmp_path / "images"
        ledger_path = tmp_path / "led.jsonl"
        self._seed_sidecar(data_dir, "casey", "alpha")
        monkeypatch.setattr(settings, "image_storage_path", str(data_dir))

        first = ledger.backfill_from_gallery(data_dir=data_dir, ledger_path=ledger_path)
        second = ledger.backfill_from_gallery(data_dir=data_dir, ledger_path=ledger_path)

        assert first["seeded"] == 1
        assert second["seeded"] == 0
        assert second["skipped"] == 1
        # Still exactly one line — no double-seed.
        assert len(ledger.read_records(path=ledger_path)) == 1
