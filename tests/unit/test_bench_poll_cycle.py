"""Unit tests for the pure helpers behind benchmarks/bench_poll_cycle.py."""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from benchmarks.poll_cycle.fake_gh import install_fake_gh
from benchmarks.poll_cycle.metrics import (
    EndpointResult,
    LagMonitor,
    PhaseResult,
    SpawnCounter,
    format_table,
)
from benchmarks.poll_cycle.stub_github import N_REPOS, StubGitHub, respond

pytestmark = pytest.mark.unit


async def test_stub_serves_org_sized_responses_and_counts_calls() -> None:
    assert respond("https://api.github.com/orgs/acme/repos?page=2") == (200, [])
    stub = StubGitHub(latency_s=0)
    transport = httpx.MockTransport(stub.handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as client:
        assert len((await client.get("/orgs/acme/repos")).json()) == N_REPOS
    assert stub.calls == 1


async def test_lag_monitor_records_a_blocked_loop() -> None:
    monitor = LagMonitor()
    monitor.start()
    await asyncio.sleep(0.02)
    monitor.max_lag_s = 0.0
    time.sleep(0.06)  # blocks the loop on purpose
    await asyncio.sleep(0.02)
    await monitor.stop()
    assert monitor.max_lag_s >= 0.04


def test_spawn_counter_separates_gh_from_other(tmp_path: Path) -> None:
    fake = install_fake_gh(tmp_path / "bin", Path(__file__).resolve().parents[2], 0)
    counter = SpawnCounter()
    original = counter.popen_cls.__init__
    with counter.installed():
        subprocess.run([str(fake), "auth", "status"], capture_output=True, check=False)
        subprocess.run(["true"], check=False)
    assert counter.popen_cls.__init__ is original
    assert (counter.gh, counter.other) == (1, 1)


def test_format_table_aligns_columns_and_totals_warm_calls() -> None:
    cold = PhaseResult(200, 10, 2, 1, 0, 3)
    warm = PhaseResult(200, 5, 0, 4, 0, 1)
    lines = format_table([EndpointResult("/api/x", cold, warm)]).splitlines()
    assert len(lines) == 2 and len(lines[0]) == len(lines[1])
    assert lines[1].startswith("/api/x") and lines[1].split()[-2] == "4"
