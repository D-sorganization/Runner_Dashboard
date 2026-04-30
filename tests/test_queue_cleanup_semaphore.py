"""Verify ``queue_cleanup.find_stale_runs`` bounds fan-out (#393).

The function scans every repo in the org for queued runs.  Without a
semaphore the ``asyncio.gather`` would dispatch hundreds of concurrent
``gh`` subprocesses which is hostile to both GitHub and the local box.
This test asserts both the source-level guard and the runtime behaviour.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import queue_cleanup

QUEUE_CLEANUP_SRC = Path(queue_cleanup.__file__).read_text(encoding="utf-8")


def test_module_imports_asyncio_semaphore() -> None:
    assert "asyncio.Semaphore" in QUEUE_CLEANUP_SRC


def test_find_stale_runs_uses_semaphore() -> None:
    src = inspect.getsource(queue_cleanup.find_stale_runs)
    assert "asyncio.Semaphore" in src, "find_stale_runs must construct a Semaphore"
    assert "async with sem" in src, "find_stale_runs must acquire the semaphore"
    assert "asyncio.gather" in src, "find_stale_runs must still gather results"


def test_scan_concurrency_is_capped_at_8() -> None:
    """At most 8 concurrent repo queries (per #393 partial scope)."""
    assert queue_cleanup._SCAN_CONCURRENCY <= 8


def test_semaphore_actually_limits_concurrency() -> None:
    """Runtime check: never more than _SCAN_CONCURRENCY in-flight at once."""
    repos = [f"repo-{i}" for i in range(40)]
    in_flight = 0
    peak = 0

    async def fake_query(_org: str, _repo: str, _min_age):  # type: ignore[no-untyped-def]
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return []

    async def fake_list_repos(_org: str) -> list[str]:
        return repos

    async def run() -> None:
        # Patch the module-level helpers used inside find_stale_runs.
        orig_list = queue_cleanup.list_all_repos
        orig_query = queue_cleanup._queued_stale_for_repo
        queue_cleanup.list_all_repos = fake_list_repos  # type: ignore[assignment]
        queue_cleanup._queued_stale_for_repo = fake_query  # type: ignore[assignment]
        try:
            await queue_cleanup.find_stale_runs("dummy-org", min_age_minutes=60)
        finally:
            queue_cleanup.list_all_repos = orig_list  # type: ignore[assignment]
            queue_cleanup._queued_stale_for_repo = orig_query  # type: ignore[assignment]

    asyncio.run(run())

    assert peak <= queue_cleanup._SCAN_CONCURRENCY, (
        f"peak in-flight {peak} exceeded cap {queue_cleanup._SCAN_CONCURRENCY}"
    )
