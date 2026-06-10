"""Tests for the cached runner-capacity snapshot (cold /api/fleet/status 504 fix).

``get_runner_capacity_snapshot`` is embedded in every ``/api/system`` and
``/api/fleet/status`` response. Its build path forks the runner-scheduler binary
plus two ``systemctl is-active`` calls (~2-3 s on a busy WSL host), which
dominated endpoint latency and pushed ``/api/fleet/status`` past its 15 s
budget. These tests pin the caching contract that collapses that per-poll fork
cost.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def test_runner_capacity_snapshot_is_cached(monkeypatch) -> None:
    """Repeated calls within the TTL must build the snapshot exactly once."""
    import cache_utils
    import server

    cache_utils.cache_delete("runner_capacity")
    build_calls = []

    def fake_build() -> dict:
        build_calls.append(1)
        return {"machine": "test-host", "timers": {"runner-scheduler.timer": "active"}}

    monkeypatch.setattr(server, "_build_runner_capacity_snapshot", fake_build)

    first = server.get_runner_capacity_snapshot()
    second = server.get_runner_capacity_snapshot()
    third = server.get_runner_capacity_snapshot()

    assert first == second == third
    assert first["machine"] == "test-host"
    # The expensive build runs once; subsequent calls hit the cache.
    assert len(build_calls) == 1


def test_runner_capacity_snapshot_rebuilds_after_ttl(monkeypatch) -> None:
    """Once the TTL lapses, the snapshot is rebuilt (kept effectively live)."""
    import cache_utils
    import server

    cache_utils.cache_delete("runner_capacity")
    build_calls = []

    def fake_build() -> dict:
        build_calls.append(1)
        return {"machine": "test-host", "n": len(build_calls)}

    monkeypatch.setattr(server, "_build_runner_capacity_snapshot", fake_build)
    # Force every cache read to be treated as expired.
    monkeypatch.setattr(server.CacheTtl, "RUNNER_CAPACITY_S", 0)

    server.get_runner_capacity_snapshot()
    server.get_runner_capacity_snapshot()
    assert len(build_calls) == 2


def test_runner_capacity_snapshot_shape_preserved(monkeypatch) -> None:
    """The cached wrapper must not alter the snapshot's keys/shape."""
    import cache_utils
    import server

    cache_utils.cache_delete("runner_capacity")
    payload = {
        "machine": "h",
        "aliases": ["a"],
        "configured_runners": 8,
        "timers": {"runner-scheduler.timer": "active", "runner-cleanup.timer": "inactive"},
        "schedule": {},
        "state": {"available": True},
    }
    monkeypatch.setattr(server, "_build_runner_capacity_snapshot", lambda: payload)
    result = server.get_runner_capacity_snapshot()
    assert result == payload
