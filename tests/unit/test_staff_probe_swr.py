"""Slow CLI OAuth probes must not freeze the event loop (stale-while-revalidate)."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid

import pytest
from staff import probe_swr

pytestmark = pytest.mark.unit

SLOW_PROBE_S = 0.5


def _key() -> str:
    return f"test-swr:{uuid.uuid4().hex}"


def _slow_factory(calls: list[str], value: tuple[bool, str], started: threading.Event | None = None):
    def factory() -> tuple[bool, str]:
        calls.append(threading.current_thread().name)
        if started is not None:
            started.set()
        time.sleep(SLOW_PROBE_S)
        return value

    return factory


def test_off_loop_caller_blocks_and_gets_fresh_value() -> None:
    key = _key()
    calls: list[str] = []
    probe_swr.store(key, (False, "old"))
    probe_swr.expire(key)
    assert probe_swr.swr_probe(key, 60, _slow_factory(calls, (True, "new"))) == (True, "new")
    assert calls == [threading.current_thread().name]


def test_fresh_value_is_served_without_probing() -> None:
    key = _key()
    calls: list[str] = []
    probe_swr.store(key, (True, "cached"))
    assert probe_swr.swr_probe(key, 60, _slow_factory(calls, (False, "x"))) == (True, "cached")
    assert calls == []


@pytest.mark.asyncio
async def test_on_loop_stale_value_returns_immediately_and_refreshes_in_background() -> None:
    key = _key()
    calls: list[str] = []
    started = threading.Event()
    probe_swr.store(key, (True, "stale"))
    probe_swr.expire(key)

    t0 = time.perf_counter()
    result = probe_swr.swr_probe(key, 60, _slow_factory(calls, (False, "fresh"), started))
    elapsed = time.perf_counter() - t0

    assert result == (True, "stale")
    assert elapsed < SLOW_PROBE_S / 2, f"event loop blocked for {elapsed:.3f}s"
    assert started.wait(2.0)
    assert calls and calls[0] != threading.current_thread().name
    for _ in range(100):
        if probe_swr.swr_probe(key, 60, _slow_factory(calls, (True, "unused"))) == (False, "fresh"):
            break
        await asyncio.sleep(0.02)
    assert probe_swr.swr_probe(key, 60, _slow_factory(calls, (True, "unused"))) == (False, "fresh")


@pytest.mark.asyncio
async def test_on_loop_concurrent_misses_start_one_background_refresh() -> None:
    key = _key()
    calls: list[str] = []
    probe_swr.store(key, (True, "stale"))
    probe_swr.expire(key)
    for _ in range(5):
        assert probe_swr.swr_probe(key, 60, _slow_factory(calls, (True, "fresh"))) == (True, "stale")
    await asyncio.sleep(0.05)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_on_loop_first_probe_still_blocks_when_nothing_is_known() -> None:
    key = _key()
    calls: list[str] = []
    assert probe_swr.swr_probe(key, 60, _slow_factory(calls, (True, "first"))) == (True, "first")
    assert calls == [threading.current_thread().name]


def test_forget_drops_last_known_value() -> None:
    key = _key()
    probe_swr.store(key, (True, "known"))
    probe_swr.forget(key)
    calls: list[str] = []
    assert probe_swr.swr_probe(key, 60, _slow_factory(calls, (False, "probed"))) == (False, "probed")
    assert len(calls) == 1


def test_background_failure_is_contained() -> None:
    done = threading.Event()

    def boom() -> None:
        try:
            raise RuntimeError("probe exploded")
        finally:
            done.set()

    key = _key()
    assert probe_swr.run_in_background(key, boom) is True
    assert done.wait(2.0)
    for _ in range(50):
        if probe_swr.run_in_background(key, lambda: None):
            break
        time.sleep(0.01)
    else:
        pytest.fail("in-flight marker was not cleared after a failing probe")
