"""GET claim-check cache: single-flight, TTL, stale fallback (#4592)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from coordination import claims_check_cache as cache


def _payload(held: bool = False) -> dict:
    return {"available": True, "held": held, "agent": "", "session": "", "reason": "", "expires_at": None}


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    cache.reset()
    yield
    cache.reset()


def test_concurrent_reads_coalesce_one_loader_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []
    started = threading.Event()
    gate = threading.Event()

    def slow_check(repo: str, issue: int) -> dict:
        calls.append((repo, issue))
        started.set()
        gate.wait(timeout=5.0)
        return _payload()

    monkeypatch.setattr(cache, "_loader", slow_check)
    monkeypatch.setenv("CLAIMS_CHECK_REQUEST_BUDGET_SECONDS", "30")
    monkeypatch.setenv("CLAIMS_CHECK_CACHE_TTL_SECONDS", "45")

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(cache.read, "Tools", 12) for _ in range(8)]
        assert started.wait(timeout=2.0)
        gate.set()
        results = [f.result(timeout=5.0) for f in as_completed(futures)]

    assert len(calls) == 1
    assert all(r["stale"] is False and r["held"] is False for r in results)


def test_ttl_serves_fresh_without_second_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def check(_repo: str, _issue: int) -> dict:
        nonlocal calls
        calls += 1
        return _payload(held=True)

    monkeypatch.setattr(cache, "_loader", check)
    monkeypatch.setenv("CLAIMS_CHECK_CACHE_TTL_SECONDS", "60")

    first = cache.read("Tools", 3)
    second = cache.read("Tools", 3)

    assert calls == 1
    assert first["stale"] is False
    assert second["stale"] is False
    assert first["as_of"] == second["as_of"]


def test_ttl_expiry_triggers_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    t = {"now": 1000.0}

    def clock() -> float:
        return t["now"]

    calls = 0

    def check(_repo: str, _issue: int) -> dict:
        nonlocal calls
        calls += 1
        return _payload(held=calls > 1)

    monkeypatch.setattr(cache, "_loader", check)
    monkeypatch.setattr(cache, "_clock", clock)
    monkeypatch.setenv("CLAIMS_CHECK_CACHE_TTL_SECONDS", "10")
    monkeypatch.setenv("CLAIMS_CHECK_REQUEST_BUDGET_SECONDS", "30")

    cache.read("Tools", 5)
    t["now"] += 11.0
    refreshed = cache.read("Tools", 5)

    assert calls == 2
    assert refreshed["held"] is True


def test_invalidate_forces_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def check(_repo: str, _issue: int) -> dict:
        nonlocal calls
        calls += 1
        return _payload()

    monkeypatch.setattr(cache, "_loader", check)
    cache.read("Tools", 9)
    cache.invalidate("Tools", 9)
    cache.read("Tools", 9)
    assert calls == 2


def test_invalidate_repo_clears_all_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def check(_repo: str, issue: int) -> dict:
        calls.append(issue)
        return _payload()

    monkeypatch.setattr(cache, "_loader", check)
    cache.read("Tools", 1)
    cache.read("Tools", 2)
    cache.invalidate_repo("Tools")
    cache.read("Tools", 1)
    assert calls.count(1) == 2 and calls.count(2) == 1


def test_stale_fallback_when_budget_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    t = {"now": 0.0}
    monkeypatch.setattr(cache, "_clock", lambda: t["now"])
    gate = threading.Event()

    def fast_check(_repo: str, _issue: int) -> dict:
        return _payload()

    def slow_check(_repo: str, _issue: int) -> dict:
        gate.wait(timeout=5.0)
        return _payload(held=True)

    monkeypatch.setattr(cache, "_loader", fast_check)
    monkeypatch.setenv("CLAIMS_CHECK_REQUEST_BUDGET_SECONDS", "0.15")
    monkeypatch.setenv("CLAIMS_CHECK_CACHE_TTL_SECONDS", "60")
    cache.read("Tools", 44)

    t["now"] = 120.0
    monkeypatch.setattr(cache, "_loader", slow_check)
    stale = cache.read("Tools", 44)
    gate.set()

    assert stale["stale"] is True
    assert "age_sec" in stale
    assert stale["held"] is False


def test_unavailable_without_prior_result(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()

    def slow_check(_repo: str, _issue: int) -> dict:
        gate.wait(timeout=5.0)
        return _payload()

    monkeypatch.setattr(cache, "_loader", slow_check)
    monkeypatch.setenv("CLAIMS_CHECK_REQUEST_BUDGET_SECONDS", "0.05")

    with pytest.raises(cache.ClaimCheckUnavailable) as excinfo:
        cache.read("Tools", 77)

    assert excinfo.value.retry_after >= 1.0
    gate.set()  # let background work finish


def test_preserves_existing_response_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "available": True,
        "held": True,
        "agent": "codex",
        "session": "codex-1",
        "reason": "active lease",
        "expires_at": "2026-09-23T12:00:00Z",
    }

    monkeypatch.setattr(cache, "_loader", lambda _r, _i: dict(body))
    out = cache.read("Tools", 12)

    for key, value in body.items():
        assert out[key] == value
    assert out["stale"] is False
    assert "as_of" in out
