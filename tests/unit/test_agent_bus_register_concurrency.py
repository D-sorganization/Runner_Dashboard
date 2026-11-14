"""RD #4274 B-12: concurrent register for one session must not displace the first owner."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from agent_bus.store import AgentBusStore, LiveSessionConflictError
from identity import Principal, identity_manager
from staff import store as staff_store_mod

_SESSION = "cursor-node-c-20261112-concurrent-b12"
_SAFETY_TIMEOUT = 10.0


class _SpyLock:
    """RLock proxy that fires ``contended`` once a second thread asks for the lock."""

    def __init__(self) -> None:
        self._inner = threading.RLock()
        self._seen: set[int] = set()
        self._guard = threading.Lock()
        self.contended = threading.Event()

    def acquire(self, *args: Any, **kwargs: Any) -> bool:
        with self._guard:
            self._seen.add(threading.get_ident())
            if len(self._seen) >= 2:
                self.contended.set()
        return self._inner.acquire(*args, **kwargs)

    def release(self) -> None:
        self._inner.release()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


@pytest.fixture
def bus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentBusStore:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    staff_store_mod.reset_store()
    return AgentBusStore(tmp_path / "bus.sqlite3")


def _fleet_principal() -> Principal:
    return Principal(id="agent-fleet", type="bot", name="fleet", roles=["bot"])


@pytest.mark.unit
def test_concurrent_register_same_session_only_first_wins(bus: AgentBusStore) -> None:
    """Two racing fleet-peer registers: one row, the loser gets LiveSessionConflictError."""
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    guard = threading.Lock()

    def attempt() -> None:
        barrier.wait(timeout=_SAFETY_TIMEOUT)
        try:
            bus.register(
                session_id=_SESSION,
                role="local",
                provider="cursor",
                node="node-c",
                principal=_fleet_principal(),
                lease_seconds=600,
                mint_token=True,
            )
            with guard:
                outcomes.append("ok")
        except LiveSessionConflictError:
            with guard:
                outcomes.append("conflict")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(_SAFETY_TIMEOUT)

    assert sorted(outcomes) == ["conflict", "ok"]
    row = bus.get_principal(_SESSION)
    assert row is not None
    assert row.principal_id == f"fleet-session:{_SESSION}"


@pytest.mark.unit
def test_register_conflict_check_held_while_minting(bus: AgentBusStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """While the first register still holds the store lock during token mint, the second must not upsert."""
    spy = _SpyLock()
    bus._lock = spy  # type: ignore[assignment]
    reached_mint = threading.Event()
    release_mint = threading.Event()
    mint_guard = threading.Lock()
    mint_count = 0
    real_add = identity_manager.add_ephemeral_token

    def slow_mint(*args: Any, **kwargs: Any) -> None:
        nonlocal mint_count
        with mint_guard:
            mint_count += 1
            first = mint_count == 1
        if first:
            reached_mint.set()
            assert spy.contended.wait(_SAFETY_TIMEOUT), "second register never contended on lock"
            assert release_mint.wait(_SAFETY_TIMEOUT)
        real_add(*args, **kwargs)

    monkeypatch.setattr(identity_manager, "add_ephemeral_token", slow_mint)

    outcomes: list[str] = []
    out_guard = threading.Lock()

    def attempt() -> None:
        try:
            bus.register(
                session_id=_SESSION,
                role="local",
                provider="cursor",
                node="node-c",
                principal=_fleet_principal(),
                lease_seconds=600,
                mint_token=True,
            )
            with out_guard:
                outcomes.append("ok")
        except LiveSessionConflictError:
            with out_guard:
                outcomes.append("conflict")

    first = threading.Thread(target=attempt)
    first.start()
    assert reached_mint.wait(_SAFETY_TIMEOUT)
    second = threading.Thread(target=attempt)
    second.start()
    assert spy.contended.wait(_SAFETY_TIMEOUT)
    release_mint.set()
    first.join(_SAFETY_TIMEOUT)
    second.join(_SAFETY_TIMEOUT)

    assert sorted(outcomes) == ["conflict", "ok"]
    assert bus.get_principal(_SESSION) is not None
