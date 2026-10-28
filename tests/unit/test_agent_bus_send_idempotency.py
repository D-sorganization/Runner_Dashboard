"""RD #3024: concurrent duplicate agent-bus sends replay instead of raising IntegrityError."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from agent_bus import store as store_mod
from agent_bus.models import SendBody
from agent_bus.store import AgentBusStore
from identity import Principal
from staff import store as staff_store_mod

_SESSION = "pr-remediator-20261026-a"
_KEY = "idem-3024-concurrent"
_SAFETY_TIMEOUT = 10.0


class _SpyLock:
    """RLock proxy that fires ``contended`` once a second thread asks for the lock.

    Replaces sleeps: the first sender is held at the SELECT->INSERT gap until the
    second sender has demonstrably entered ``send`` (its first lock acquire).
    """

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
    store_mod.reset_store()
    store = AgentBusStore(tmp_path / "bus.sqlite3")
    store.register(
        session_id=_SESSION,
        role="pr-remediator",
        provider="cursor",
        node="Hub",
        principal=Principal(id="agent-pr-remediator", type="bot", name="pr-remediator", roles=["bot"]),
        lease_seconds=600,
        mint_token=False,
    )
    return store


def _body() -> SendBody:
    return SendBody(
        session=_SESSION,
        to="issue-remediator",
        type="status",
        repo="Runner_Dashboard",
        work_kind="issue",
        work_number=3024,
        body="concurrent duplicate",
        idempotency_key=_KEY,
    )


def _row_count(store: AgentBusStore) -> int:
    conn = store._conn()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM agent_bus_inbox").fetchone()[0])
    finally:
        conn.close()


@pytest.mark.unit
def test_send_idempotency_interleaved(bus: AgentBusStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two senders interleaved across the SELECT/INSERT gap yield one row and one replay."""
    spy = _SpyLock()
    bus._lock = spy  # type: ignore[assignment]
    real_ensure = store_mod.ensure_work_thread
    first_in_gap = threading.Event()
    gate_owner: list[int] = []
    gate_guard = threading.Lock()

    def gated_ensure(*args: Any, **kwargs: Any) -> Any:
        # The first sender to reach the gap waits until the other has begun send().
        with gate_guard:
            is_first = not gate_owner
            if is_first:
                gate_owner.append(threading.get_ident())
        if is_first:
            first_in_gap.set()
            assert spy.contended.wait(_SAFETY_TIMEOUT), "second sender never entered send()"
        return real_ensure(*args, **kwargs)

    monkeypatch.setattr(store_mod, "ensure_work_thread", gated_ensure)

    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []
    out_guard = threading.Lock()

    def run() -> None:
        try:
            res = bus.send(_body())
        except BaseException as exc:  # noqa: BLE001 - the test asserts none escape
            with out_guard:
                errors.append(exc)
            return
        with out_guard:
            results.append(res)

    first = threading.Thread(target=run)
    first.start()
    assert first_in_gap.wait(_SAFETY_TIMEOUT)
    second = threading.Thread(target=run)
    second.start()
    first.join(_SAFETY_TIMEOUT)
    second.join(_SAFETY_TIMEOUT)

    assert not errors, errors
    assert len(results) == 2
    assert len({r["message"]["id"] for r in results}) == 1
    assert sorted(r["replayed"] for r in results) == [False, True]
    assert _row_count(bus) == 1


@pytest.mark.unit
def test_send_replays_when_competing_insert_lands_in_gap(bus: AgentBusStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """A row committed by another writer after our SELECT is replayed, never an IntegrityError."""
    real_ensure = store_mod.ensure_work_thread

    def competing_writer(*args: Any, **kwargs: Any) -> Any:
        thread_id = real_ensure(*args, **kwargs)
        conn = bus._conn()
        try:
            conn.execute(
                "INSERT INTO agent_bus_inbox "
                "(message_id, idempotency_key, from_session, to_address, msg_type, repo, work_kind, work_number, "
                "body_md, thread_id, thread_message_id, created_at) "
                "VALUES ('abm_competitor01', ?, ?, 'issue-remediator', 'status', 'Runner_Dashboard', 'issue', "
                "3024, 'winner', ?, '', '2026-10-26T00:00:00+00:00')",
                (_KEY, _SESSION, thread_id),
            )
        finally:
            conn.close()
        return thread_id

    monkeypatch.setattr(store_mod, "ensure_work_thread", competing_writer)

    result = bus.send(_body())

    assert result["replayed"] is True
    assert result["message"]["id"] == "abm_competitor01"
    assert _row_count(bus) == 1
