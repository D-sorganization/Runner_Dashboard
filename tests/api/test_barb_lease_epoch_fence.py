"""Barb-authored writes carry the lease epoch and get 409 when stale (#2734).

A request declares Barb authority with ``X-Barb-Scope`` + ``X-Barb-Lease-Epoch``. The hub validates
holder, expiry and epoch under the continuity store lock, in the same critical section as the write.
Requests that do not declare it are unaffected.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from identity import Principal, identity_manager

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.32", 52032)
SCOPE = "barb:pr-queue"
pytestmark = pytest.mark.integration

_MEM = ["memory.read", "memory.write", "memory.shared.summarize"]
_TOKENS = {
    "alpha": Principal(id="agent-alpha", type="bot", name="Alpha", roles=["bot"], scopes=_MEM),
    "beta": Principal(id="agent-beta", type="bot", name="Beta", roles=["bot"], scopes=_MEM),
    "oper": Principal(id="op-1", type="human", name="Operator", roles=["operator"], scopes=_MEM),
}


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from staff import barb_continuity  # noqa: PLC0415

    s = barb_continuity.BarbContinuityStore(tmp_path / "barb.sqlite3")
    monkeypatch.setattr(barb_continuity, "_DEFAULT_STORE", s)
    return s


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: Any) -> Iterator[TestClient]:
    import orchestration_audit  # noqa: PLC0415
    from memory.store import reset_memory_store  # noqa: PLC0415
    from staff.audit import reset_audit_store  # noqa: PLC0415

    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(tmp_path / "memory.sqlite3"))
    monkeypatch.setenv("STAFF_AUDIT_DB", str(tmp_path / "staff_audit.sqlite3"))
    monkeypatch.setattr(orchestration_audit, "ORCHESTRATION_AUDIT_PATH", tmp_path / "audit.ndjson")
    reset_memory_store()
    reset_audit_store()
    for name, prin in _TOKENS.items():
        identity_manager.add_ephemeral_token(hashlib.sha256(f"tok-{name}".encode()).hexdigest(), prin, name=name)
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)
    reset_memory_store()
    reset_audit_store()


def _h(name: str, scope: str | None = None, epoch: int | None = None, holder: str | None = None) -> dict[str, str]:
    h = {**_XHR, "Authorization": f"Bearer tok-{name}"}
    if scope is not None:
        h["X-Barb-Scope"] = scope
    if epoch is not None:
        h["X-Barb-Lease-Epoch"] = str(epoch)
    if holder is not None:
        h["X-Barb-Holder"] = holder
    return h


def _checkout(client: TestClient, who: str, **body: Any) -> dict[str, Any]:
    r = client.post("/api/v1/staff/barb/leases/checkout", headers=_h(who), json={"scope": SCOPE, **body})
    assert r.status_code == 200, r.text
    return r.json()["lease"]


_MEMORY = {"entry_type": "session_digest", "source_product": "grokbot", "body": {"summary": "x", "open_items": []}}


def _memory_count(client: TestClient) -> int:
    return len(client.get("/api/v1/memory", headers=_h("alpha")).json()["entries"])


def _stale(resp: Any, reason: str, current_epoch: int, holder: str) -> None:
    assert resp.status_code == 409, resp.text
    d = resp.json()["detail"]
    assert d["error"] == "stale_barb_lease"
    assert d["reason"] == reason
    assert d["current_epoch"] == current_epoch
    assert d["holder"] == holder


def test_holder_with_current_epoch_writes(client: TestClient) -> None:
    lease = _checkout(client, "alpha")
    r = client.post("/api/v1/memory", headers=_h("alpha", SCOPE, lease["epoch"]), json=_MEMORY)
    assert r.status_code == 201, r.text
    assert _memory_count(client) == 1


def test_stale_epoch_after_takeover_is_409_and_writes_nothing(client: TestClient) -> None:
    lease = _checkout(client, "alpha")
    new = _checkout(client, "oper", takeover=True, reason="alpha wedged")
    assert new["epoch"] == lease["epoch"] + 1
    r = client.post("/api/v1/memory", headers=_h("alpha", SCOPE, lease["epoch"]), json=_MEMORY)
    _stale(r, "holder_mismatch", new["epoch"], "op-1")
    assert _memory_count(client) == 0


def test_takeover_holder_writes_with_new_epoch(client: TestClient) -> None:
    _checkout(client, "alpha")
    new = _checkout(client, "oper", takeover=True, reason="alpha wedged")
    ok = client.post("/api/v1/memory", headers=_h("oper", SCOPE, new["epoch"]), json=_MEMORY)
    assert ok.status_code == 201, ok.text
    old_epoch = client.post("/api/v1/memory", headers=_h("oper", SCOPE, new["epoch"] - 1), json=_MEMORY)
    _stale(old_epoch, "stale_epoch", new["epoch"], "op-1")


def test_expired_lease_is_409(client: TestClient, store: Any) -> None:
    past = datetime.now(UTC) - timedelta(hours=2)
    lease = store.checkout_lease(scope=SCOPE, holder="agent-alpha", ttl_seconds=60, now=past)["lease"]
    r = client.post("/api/v1/memory", headers=_h("alpha", SCOPE, lease["epoch"]), json=_MEMORY)
    _stale(r, "expired", lease["epoch"], "agent-alpha")


def test_different_principal_claiming_the_scope_is_409(client: TestClient) -> None:
    lease = _checkout(client, "alpha")
    r = client.post("/api/v1/memory", headers=_h("beta", SCOPE, lease["epoch"]), json=_MEMORY)
    _stale(r, "holder_mismatch", lease["epoch"], "agent-alpha")


def test_claiming_a_scope_nobody_holds_is_409(client: TestClient) -> None:
    r = client.post("/api/v1/memory", headers=_h("alpha", "barb:night-watch", 1), json=_MEMORY)
    _stale(r, "no_lease", 0, "")


def test_non_barb_request_is_unaffected_even_with_a_stale_seat(client: TestClient) -> None:
    _checkout(client, "alpha")
    _checkout(client, "oper", takeover=True, reason="alpha wedged")
    r = client.post("/api/v1/memory", headers=_h("alpha"), json=_MEMORY)
    assert r.status_code == 201, r.text


def test_incomplete_or_invalid_declaration_is_422(client: TestClient) -> None:
    assert client.post("/api/v1/memory", headers=_h("alpha", SCOPE), json=_MEMORY).status_code == 422
    assert client.post("/api/v1/memory", headers=_h("alpha", epoch=1), json=_MEMORY).status_code == 422
    assert client.post("/api/v1/memory", headers=_h("alpha", SCOPE, 0), json=_MEMORY).status_code == 422
    bad = {**_h("alpha", SCOPE), "X-Barb-Lease-Epoch": "abc"}
    assert client.post("/api/v1/memory", headers=bad, json=_MEMORY).status_code == 422


def test_named_seat_among_shared_token_seats(client: TestClient) -> None:
    lease = _checkout(client, "alpha", holder="agent-alpha:seat2")
    ok = client.post("/api/v1/memory", headers=_h("alpha", SCOPE, lease["epoch"], "agent-alpha:seat2"), json=_MEMORY)
    assert ok.status_code == 201, ok.text
    other = client.post("/api/v1/memory", headers=_h("alpha", SCOPE, lease["epoch"], "agent-alpha:seat9"), json=_MEMORY)
    assert other.status_code == 409
    spoof = client.post("/api/v1/memory", headers=_h("beta", SCOPE, lease["epoch"], "agent-alpha:seat2"), json=_MEMORY)
    assert spoof.status_code == 409


def test_session_digest_is_fenced(client: TestClient) -> None:
    lease = _checkout(client, "alpha")
    _checkout(client, "oper", takeover=True, reason="alpha wedged")
    digest = {"source_product": "codex", "conversation_id": "c-1", "user_message_id": "u-1", "summary": "x"}
    r = client.post("/api/v1/memory/session-digest", headers=_h("alpha", SCOPE, lease["epoch"]), json=digest)
    assert r.status_code == 409, r.text


def test_turnover_inventory_and_handoff_are_fenced(client: TestClient) -> None:
    lease = _checkout(client, "alpha")
    good = _h("alpha", SCOPE, lease["epoch"])
    turnover = {"scope": SCOPE, "summary": "shift over", "next_actions": ["x"]}
    assert client.post("/api/v1/staff/barb/turnover", headers=good, json=turnover).status_code == 200
    task = client.post("/api/v1/staff/barb/inventory", headers=good, json={"title": "t", "scope": SCOPE})
    assert task.status_code == 200, task.text
    task_id = task.json()["task"]["task_id"]
    _checkout(client, "oper", takeover=True, reason="alpha wedged")
    assert client.post("/api/v1/staff/barb/turnover", headers=good, json=turnover).status_code == 409
    assert (
        client.post("/api/v1/staff/barb/inventory", headers=good, json={"title": "t2", "scope": SCOPE}).status_code
        == 409
    )
    handoff = {"task_id": task_id, "action": "request_desktop", "actor": "alpha", "expected_epoch": 1}
    assert client.post("/api/v1/staff/barb/handoff", headers=good, json=handoff).status_code == 409
    # header scope must agree with the body scope
    mismatch = client.post(
        "/api/v1/staff/barb/turnover",
        headers=_h("oper", SCOPE, 2),
        json={**turnover, "scope": "barb:night-watch"},
    )
    assert mismatch.status_code == 422


def test_directives_write_is_fenced(client: TestClient) -> None:
    lease = _checkout(client, "oper")
    body = {"directives": [{"text": "ship it", "repo": "*", "priority": 2}]}
    ok = client.put("/api/priorities/directives", headers=_h("oper", SCOPE, lease["epoch"]), json=body)
    assert ok.status_code == 200, ok.text
    lease2 = _checkout(client, "oper", takeover=True, reason="renew with bump")
    assert lease2["epoch"] == lease["epoch"]  # same live holder renews without a bump
    stale = client.put("/api/priorities/directives", headers=_h("oper", SCOPE, lease["epoch"] + 5), json=body)
    _stale(stale, "stale_epoch", lease["epoch"], "op-1")
    plain = client.put("/api/priorities/directives", headers=_h("oper"), json=body)
    assert plain.status_code == 200, plain.text


def test_rejection_is_audited(client: TestClient) -> None:
    from staff.audit import get_audit_store  # noqa: PLC0415

    lease = _checkout(client, "alpha")
    before = len(get_audit_store().list_entries(action="barb_write_rejected", limit=500))
    client.post("/api/v1/memory", headers=_h("beta", SCOPE, lease["epoch"]), json=_MEMORY)
    rows = get_audit_store().list_entries(action="barb_write_rejected", limit=500)
    assert len(rows) == before + 1
    rows = rows[:1]  # newest first
    assert rows[0].principal == "principal:agent-beta"
    assert rows[0].outcome == "rejected"
    assert rows[0].detail["reason"] == "holder_mismatch"
    assert rows[0].detail["current_epoch"] == lease["epoch"]


# -- atomicity: a takeover cannot land between the check and the write ----------------------------------


@pytest.mark.unit
def test_takeover_waits_for_an_in_flight_fenced_write(store: Any) -> None:
    from staff.barb_lease_fence import BarbClaim, barb_write_fence  # noqa: PLC0415

    lease = store.checkout_lease(scope=SCOPE, holder="agent-alpha")["lease"]
    claim = BarbClaim(scope=SCOPE, epoch=lease["epoch"], caller_id="agent-alpha")
    in_write, release_write, order = threading.Event(), threading.Event(), []

    def writer() -> None:
        with barb_write_fence(claim, surface="t", target="t", principal="p", store=store):
            in_write.set()  # the check passed; the write is "in flight"
            release_write.wait(5)
            order.append("write")

    def taker() -> None:
        store.checkout_lease(scope=SCOPE, holder="op-1", force=True)
        order.append("takeover")

    wt = threading.Thread(target=writer)
    wt.start()
    assert in_write.wait(5)
    tt = threading.Thread(target=taker)
    tt.start()
    tt.join(0.3)
    assert tt.is_alive(), "takeover must block while a fenced write holds the lease"
    release_write.set()
    wt.join(5)
    tt.join(5)
    assert order == ["write", "takeover"]
    # and the next write by the old holder is rejected
    with (
        pytest.raises(HTTPException) as err,
        barb_write_fence(claim, surface="t", target="t", principal="p", store=store),
    ):
        pytest.fail("stale write must not run")
    assert err.value.status_code == 409


@pytest.mark.unit
def test_two_writers_racing_a_takeover_never_both_succeed_after_it(store: Any) -> None:
    from staff.barb_lease_fence import BarbClaim, barb_write_fence  # noqa: PLC0415

    lease = store.checkout_lease(scope=SCOPE, holder="agent-alpha")["lease"]
    claim = BarbClaim(scope=SCOPE, epoch=lease["epoch"], caller_id="agent-alpha")
    log: list[str] = []
    start = threading.Barrier(9)

    def write(i: int) -> None:
        start.wait(5)
        try:
            with barb_write_fence(claim, surface="t", target="t", principal="p", store=store):
                log.append(f"w{i}")
        except HTTPException:
            log.append(f"r{i}")

    def take() -> None:
        start.wait(5)
        store.checkout_lease(scope=SCOPE, holder="op-1", force=True)
        log.append("T")

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)] + [threading.Thread(target=take)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(log) == 9
    after = log[log.index("T") + 1 :]
    assert all(x.startswith("r") for x in after), log  # no write lands after the takeover
