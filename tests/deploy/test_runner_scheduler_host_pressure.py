"""Host-pressure guard contract (#4311)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


host_pressure = _load_module("runner_scheduler_host_pressure_test", DEPLOY / "runner_scheduler_host_pressure.py")
runner_scheduler = _load_module("runner_scheduler_pressure_test", DEPLOY / "runner-scheduler.py")

HOST = {
    "max_host_cpu_percent": 85,
    "max_host_ram_percent": 80,
    "sustain_seconds": 60,
    "recovery_seconds": 120,
    "min_online": 2,
    "cooldown_seconds": 30,
}
FLOOR = {"host": HOST, "runners": 1}


def _write_cache(path: Path, *, cpu: float, mem: float, sampled_at: float) -> None:
    from datetime import UTC, datetime

    payload = {
        "sampled_at": datetime.fromtimestamp(sampled_at, tz=UTC).isoformat(),
        "windows": {"cpu_percent": cpu, "memory_percent": mem},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


@pytest.fixture
def pressure_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache = tmp_path / "host_metrics.json"
    latch = tmp_path / "host-pressure.json"
    monkeypatch.setattr(host_pressure, "host_metrics_cache_path", lambda: cache)
    monkeypatch.setattr(host_pressure, "HOST_PRESSURE_LATCH_PATH", latch)
    monkeypatch.setattr(host_pressure, "HOST_METRICS_STALE_S", 90.0)
    return cache, latch


def test_stale_cache_does_not_shed(pressure_env, monkeypatch: pytest.MonkeyPatch) -> None:
    cache, _ = pressure_env
    now = 1_000_000.0
    _write_cache(cache, cpu=99.0, mem=99.0, sampled_at=now - 200.0)
    desired, reason = host_pressure.apply_host_pressure_guard(FLOOR, 6, "default", now=now)
    assert desired == 6
    assert reason == "default"


def test_sustained_breach_sheds_one_runner_per_tick(pressure_env) -> None:
    cache, _ = pressure_env
    t0 = 1_000_000.0
    _write_cache(cache, cpu=90.0, mem=50.0, sampled_at=t0)
    assert host_pressure.apply_host_pressure_guard(FLOOR, 6, "default", now=t0) == (6, "default")
    _write_cache(cache, cpu=90.0, mem=50.0, sampled_at=t0 + 61)
    desired, reason = host_pressure.apply_host_pressure_guard(FLOOR, 6, "default", now=t0 + 61)
    assert desired == 5
    assert "host-pressure" in reason
    _write_cache(cache, cpu=90.0, mem=50.0, sampled_at=t0 + 122)
    desired2, _ = host_pressure.apply_host_pressure_guard(FLOOR, 6, "default", now=t0 + 122)
    assert desired2 == 4


def test_recovery_requires_sustain_window(pressure_env) -> None:
    cache, _ = pressure_env
    t0 = 1_000_000.0
    _write_cache(cache, cpu=90.0, mem=50.0, sampled_at=t0)
    host_pressure.apply_host_pressure_guard(FLOOR, 6, "default", now=t0)
    _write_cache(cache, cpu=90.0, mem=50.0, sampled_at=t0 + 61)
    host_pressure.apply_host_pressure_guard(FLOOR, 6, "default", now=t0 + 61)
    t_healthy = t0 + 95
    _write_cache(cache, cpu=10.0, mem=10.0, sampled_at=t_healthy)
    held, _ = host_pressure.apply_host_pressure_guard(FLOOR, 6, "default", now=t_healthy)
    assert held == 5
    t_clear = t_healthy + HOST["recovery_seconds"] + 5
    _write_cache(cache, cpu=10.0, mem=10.0, sampled_at=t_clear)
    cleared, reason = host_pressure.apply_host_pressure_guard(FLOOR, 6, "default", now=t_clear)
    assert cleared == 6
    assert reason == "default"


def test_oscillation_before_sustain_does_not_latch(pressure_env) -> None:
    cache, latch = pressure_env
    t0 = 2_000_000.0
    for offset, cpu in ((0, 90.0), (30, 10.0), (50, 90.0), (80, 10.0)):
        _write_cache(cache, cpu=cpu, mem=40.0, sampled_at=t0 + offset)
        host_pressure.apply_host_pressure_guard(FLOOR, 6, "default", now=t0 + offset)
    assert not latch.exists()


def test_apply_resource_floor_invokes_host_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_guard(floor, desired, reason, now=None):  # noqa: ANN001
        calls.append(desired)
        return desired - 1, f"{reason} + host-pressure (test)"

    monkeypatch.setattr(runner_scheduler, "apply_host_pressure_guard", fake_guard)
    monkeypatch.setattr(runner_scheduler, "resource_floor_breaches", lambda _floor: [])
    desired, reason = runner_scheduler.apply_resource_floor({"resource_floor": FLOOR}, 6, "default")
    assert calls == [6]
    assert desired == 5
    assert "host-pressure" in reason
