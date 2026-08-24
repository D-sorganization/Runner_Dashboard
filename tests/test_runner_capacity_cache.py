"""Tests for the cached runner-capacity snapshot (cold /api/fleet/status 504 fix).

``get_runner_capacity_snapshot`` is embedded in every ``/api/system`` and
``/api/fleet/status`` response. Its build path forks the runner-scheduler binary
plus two ``systemctl is-active`` calls (~2-3 s on a busy WSL host), which
dominated endpoint latency and pushed ``/api/fleet/status`` past its 15 s
budget. These tests pin the caching contract that collapses that per-poll fork
cost.
"""

from __future__ import annotations

import json
import subprocess
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


def test_runner_schedule_preserves_host_maximum() -> None:
    """Dashboard schedule edits must not erase the host's safety ceiling."""
    import server

    result = server._validate_runner_schedule(
        {
            "enabled": True,
            "timezone": "America/Los_Angeles",
            "default_count": 7,
            "max_count": 6,
            "schedules": [
                {
                    "name": "peak",
                    "days": ["mon"],
                    "start": "08:00",
                    "end": "17:00",
                    "runners": 8,
                }
            ],
        }
    )

    assert result["default_count"] == 6
    assert result["max_count"] == 6
    assert result["schedules"][0]["runners"] == 6


def test_scheduler_status_uses_governed_python(tmp_path, monkeypatch) -> None:
    """Status probes must not fall back to an unsupported shebang interpreter."""
    import server

    scheduler = tmp_path / "runner-scheduler"
    scheduler.touch()
    monkeypatch.setattr(server, "RUNNER_SCHEDULER_BIN", str(scheduler))
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"desired": 2}),
        stderr="",
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return completed

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    state = server._sync_runner_scheduler_state({"default_count": 2, "max_count": 4})

    assert calls[0][0] == [sys.executable, str(scheduler), "--dry-run", "--json"]
    assert state["available"] is True


def test_capacity_snapshot_reports_effective_schedule_counts(tmp_path, monkeypatch) -> None:
    """Displayed defaults and maxima must describe the schedule being enforced."""
    import server

    for runner_number in range(1, 9):
        (tmp_path / f"runner-{runner_number}").mkdir()
    schedule = {"enabled": True, "default_count": 2, "max_count": 4, "schedules": []}
    monkeypatch.setattr(server, "RUNNER_BASE_DIR", tmp_path)
    monkeypatch.setattr(server, "NUM_RUNNERS", 8)
    monkeypatch.setattr(server, "_runner_limit", lambda: 8)
    monkeypatch.setattr(server, "_load_runner_schedule_config", lambda: schedule)
    monkeypatch.setattr(server, "_sync_runner_scheduler_state", lambda config: {"config": config})
    monkeypatch.setattr(server, "_unit_active_sync", lambda _unit: False)

    snapshot = server._build_runner_capacity_snapshot()

    assert snapshot["configured_runners"] == 2
    assert snapshot["default_runners"] == 2
    assert snapshot["max_runners"] == 4
    assert snapshot["host_runner_limit"] == 8
    assert snapshot["installed_runners"] == 8
