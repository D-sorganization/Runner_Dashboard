"""Readiness deadlines must bound both latency and outstanding work (#2681)."""

from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import AsyncMock, Mock

import health
import pytest
import readiness


@pytest.fixture(autouse=True)
def short_budgets(monkeypatch):
    monkeypatch.setattr(readiness, "COMPONENT_BUDGET_S", 0.02, raising=False)
    monkeypatch.setattr(readiness, "AGGREGATE_BUDGET_S", 0.05, raising=False)


@pytest.mark.asyncio
async def test_readyz_returns_503_naming_blocked_probe_within_budget(monkeypatch):
    release = threading.Event()
    started = threading.Event()
    calls = []

    class BlockedProbe:
        name = "blocked"

        async def check(self):
            def work():
                calls.append(1)
                started.set()
                release.wait(1)

            await asyncio.to_thread(work)
            return "ok", None

    monkeypatch.setattr(health, "get_default_probes", lambda: [BlockedProbe()])
    try:
        response = await asyncio.wait_for(health.readyz(), timeout=0.3)
        assert started.is_set()
        assert response.status_code == 503
        check = json.loads(response.body)["checks"]["blocked"]
        assert check["status"] == "timed_out"
        assert check["budget_s"] == readiness.COMPONENT_BUDGET_S
        for _ in range(10):
            assert (await health.readyz()).status_code == 503
        assert len(calls) == 1
    finally:
        release.set()
        await asyncio.sleep(0.02)
    assert (await health.readyz()).status_code == 200


@pytest.mark.asyncio
async def test_aggregate_budget_preserves_completed_checks(monkeypatch):
    monkeypatch.setattr(readiness, "COMPONENT_BUDGET_S", 0.2)
    release = asyncio.Event()

    class HealthyProbe:
        name = "healthy"

        async def check(self):
            return "ok", None

    class BlockedProbe:
        name = "aggregate-blocked"

        async def check(self):
            await release.wait()
            return "ok", None

    try:
        status, body = await asyncio.wait_for(readiness.aggregate([HealthyProbe(), BlockedProbe()]), timeout=0.15)
        assert status == 503
        assert body["checks"]["healthy"] == "ok"
        assert body["checks"]["aggregate-blocked"]["status"] == "timed_out"
    finally:
        release.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_subprocess_probe_killed_on_timeout(monkeypatch):
    proc = Mock()

    async def communicate():
        await asyncio.sleep(1)
        return b"[]", b""

    proc.communicate = AsyncMock(side_effect=communicate)
    proc.wait = AsyncMock(return_value=0)
    proc.returncode = None
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    probe = readiness.RunnerHealthProbe(cache_ttl_seconds=0, subprocess_timeout_seconds=0.01)
    status, _ = await probe.check()
    assert status != "ok"
    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_subprocess_spawn_is_not_duplicated_and_is_reaped(monkeypatch):
    release = asyncio.Event()
    proc = Mock()
    proc.returncode = None
    proc.wait = AsyncMock(return_value=0)
    proc.communicate = AsyncMock(return_value=(b"[]", b""))

    async def spawn(*_args, **_kwargs):
        await release.wait()
        return proc

    spawn_mock = AsyncMock(side_effect=spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_mock)
    probe = readiness.RunnerHealthProbe(cache_ttl_seconds=0, subprocess_timeout_seconds=0.01)
    try:
        assert (await asyncio.wait_for(readiness.aggregate([probe]), 0.3))[0] == 503
        for _ in range(10):
            assert (await readiness.aggregate([probe]))[0] == 503
        assert spawn_mock.await_count == 1
    finally:
        release.set()
        await asyncio.sleep(0.02)
    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_healthy_probes_and_liveness(monkeypatch):
    class HealthyProbe:
        name = "healthy-control"

        async def check(self):
            return "ok", None

    probe = HealthyProbe()
    assert (await readiness.aggregate([probe, probe]))[0] == 200

    def forbidden_io():
        pytest.fail("livez must not consult readiness dependencies")

    monkeypatch.setattr(health, "get_default_probes", forbidden_io)
    assert await health.livez() == {"status": "ok"}
