"""Regression tests for the PR #1935 adversarial review (#1800, BR-06)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from staff.barb_authority import (
    AuthorityClient,
    AuthorityConfig,
    AuthorityRejected,
    DeliveryOutcome,
)
from staff.barb_db import immediate_txn
from staff.barb_outbox_drain import drain_once
from staff.barb_versions import ApplyOutcome
from staff.conversations import ConversationStore
from staff.run_link import run_card_id


class CommitFailsOnce:
    """Connection wrapper whose first COMMIT raises (disk full / I/O error)."""

    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner
        self.failed = False

    def execute(self, sql: str, *a: Any) -> Any:
        if sql.strip().upper() == "COMMIT" and not self.failed:
            self.failed = True
            raise sqlite3.OperationalError("disk I/O error")
        return self._inner.execute(sql, *a)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@pytest.mark.unit
def test_failed_commit_does_not_wedge_later_transactions(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite3"
    real = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    real.row_factory = sqlite3.Row
    real.execute("CREATE TABLE t (v INTEGER)")
    conn = CommitFailsOnce(real)  # type: ignore[arg-type]
    lock = threading.RLock()

    with pytest.raises(sqlite3.OperationalError), immediate_txn(conn, lock) as c:  # type: ignore[arg-type]
        c.execute("INSERT INTO t VALUES (1)")
    assert not real.in_transaction  # the failed commit was rolled back, not left open

    with immediate_txn(conn, lock) as c:  # type: ignore[arg-type]
        c.execute("INSERT INTO t VALUES (2)")
    real.close()
    reopened = sqlite3.connect(str(path))
    assert [r[0] for r in reopened.execute("SELECT v FROM t")] == [2]  # the next write really committed


class _Client:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.sent: list[int] = []

    def post(self, kind: str, thread_id: str, run_id: str, version: int, payload: dict[str, Any]) -> DeliveryOutcome:
        self.sent.append(version)
        if self.exc:
            raise self.exc
        return DeliveryOutcome.DELIVERED


@pytest.mark.unit
def test_unexpected_error_retries_entry_and_releases_the_rest(tmp_path: Path) -> None:
    cs = ConversationStore(tmp_path / "n.sqlite3")
    box = cs.barb_outbox(max_entries=10, max_bytes=100_000, max_entry_bytes=10_000)
    for run in ("a", "b", "c"):
        box.enqueue("run_card", "thr", run, 1, {"status": "running"})
    stats = drain_once(box, _Client(ValueError("boom")), limit=10, now=1_000_000.0)
    assert stats.retried == 1 and stats.attempted == 1
    with cs._lock:
        statuses = sorted(r["status"] for r in cs._conn.execute("SELECT status FROM barb_outbox"))
    assert statuses == ["pending", "pending", "pending"]  # nothing stuck in 'delivering'
    assert box.depth() == 3  # nothing lost
    assert box.claim_due(limit=10, now=2_000_000.0)  # and everything is deliverable again


@pytest.mark.unit
@pytest.mark.parametrize("body", [[1, 2], "oops", None, 7])
def test_non_dict_json_body_is_rejected_not_attribute_error(body: Any) -> None:
    cfg = AuthorityConfig.from_env(
        {"BARB_AUTHORITY_NODE": "CT", "BARB_AUTHORITY_URL": "http://ct:1", "BARB_AUTHORITY_EPOCH": "1"},
        local_node="w",
    )
    client = AuthorityClient(cfg, lambda *_: (200, body))  # type: ignore[arg-type,return-value]
    with pytest.raises(AuthorityRejected):
        client.post("run_card", "thr", "run-1", 1, {})


@pytest.mark.unit
def test_card_version_is_not_leaked_into_message_meta(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "m.sqlite3")
    tid = store.create_thread(title="t", kind="direct", role="librarian", created_by="user:d").id
    msg, res = store.apply_run_card_update(
        thread_id=tid,
        run_id="run-1",
        message_id=run_card_id("run-1"),
        status="running",
        version=1,
        body_md="x",
        meta={"status": "running"},
        author="librarian",
    )
    assert res.outcome is ApplyOutcome.APPLIED and msg is not None
    assert "card_version" not in msg.meta and "card_version" not in msg.to_dict()["meta"]
    stored = store.get_message(run_card_id("run-1"))
    assert stored is not None and "card_version" not in stored.to_dict()["meta"]
    row = store._conn.execute("SELECT version FROM run_card_versions WHERE run_id='run-1'").fetchone()
    assert row["version"] == 1  # the version lives only in run_card_versions


@pytest.mark.unit
def test_two_connections_never_apply_the_same_version_twice(tmp_path: Path) -> None:
    path = tmp_path / "shared.sqlite3"
    a, b = ConversationStore(path), ConversationStore(path)
    tid = a.create_thread(title="t", kind="direct", role="librarian", created_by="user:d").id
    top = 15
    outcomes: dict[str, list[tuple[int, ApplyOutcome]]] = {"a": [], "b": []}
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def work(name: str, store: ConversationStore) -> None:
        try:
            barrier.wait()
            for v in range(1, top + 1):
                _, res = store.apply_run_card_update(
                    thread_id=tid,
                    run_id="run-1",
                    message_id=run_card_id("run-1"),
                    status="running" if v < top else "succeeded",
                    version=v,
                    body_md=f"v{v}",
                    meta={"status": "running" if v < top else "succeeded"},
                    author="librarian",
                )
                outcomes[name].append((v, res.outcome))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n, s)) for n, s in (("a", a), ("b", b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    for v in range(1, top + 1):
        applied = [n for n in outcomes for ver, o in outcomes[n] if ver == v and o is ApplyOutcome.APPLIED]
        assert len(applied) <= 1, f"version {v} applied by {applied}"  # I2 across connections
        others = [o for n in outcomes for ver, o in outcomes[n] if ver == v and o is not ApplyOutcome.APPLIED]
        assert all(o in (ApplyOutcome.DUPLICATE, ApplyOutcome.STALE) for o in others)
    row = a._conn.execute("SELECT version FROM run_card_versions WHERE run_id='run-1'").fetchone()
    assert row["version"] == top
    assert len(a.list_messages(tid)) == 1
    n = a._conn.execute("SELECT COUNT(*) AS n FROM barb_receipts").fetchone()["n"]
    assert n == len({v for name in outcomes for v, o in outcomes[name] if o is ApplyOutcome.APPLIED})
