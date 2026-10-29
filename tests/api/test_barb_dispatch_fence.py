"""Barb-authored staff dispatch is fenced on the lease epoch, atomically with admission (#2734)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from identity import Principal
from staff import runner as runner_mod
from staff.audit import get_audit_store, reset_audit_store
from staff.barb_continuity import BarbContinuityStore
from staff.barb_lease_fence import BarbClaim
from staff.dispatch_service import DispatchCommand, dispatch_staff_run
from staff.rate_limit import reset_rate_limiter

from tests.api.test_staff_fleet import peers, staff  # noqa: F401  (shared /run fixtures)

SCOPE = "barb:dispatch-coordinator"
CALLER = Principal(id="agent-alpha", type="bot", name="Alpha", roles=["bot"], scopes=["staff.dispatch"])


@pytest.fixture(autouse=True)
def _isolated_policy_state() -> Any:
    reset_audit_store()
    reset_rate_limiter()
    yield
    reset_audit_store()
    reset_rate_limiter()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BarbContinuityStore:
    from staff import barb_continuity  # noqa: PLC0415

    s = BarbContinuityStore(tmp_path / "barb.sqlite3")
    monkeypatch.setattr(barb_continuity, "_DEFAULT_STORE", s)
    return s


@pytest.fixture
def launches(staff: runner_mod.StaffRunner, monkeypatch: pytest.MonkeyPatch) -> list[str]:  # noqa: F811
    started: list[str] = []
    monkeypatch.setattr(staff, "launch", lambda rec, plan: started.append(rec.id))
    return started


def _cmd() -> DispatchCommand:
    return DispatchCommand(role="ad-hoc", requested_by="agent-alpha", prompt="hi")


def _claim(epoch: int, caller: str = "agent-alpha") -> BarbClaim:
    return BarbClaim(scope=SCOPE, epoch=epoch, caller_id=caller)


@pytest.mark.unit
async def test_current_epoch_dispatch_is_admitted(
    store: BarbContinuityStore,
    staff: runner_mod.StaffRunner,  # noqa: F811
    launches: list[str],
) -> None:
    lease = store.checkout_lease(scope=SCOPE, holder="agent-alpha")["lease"]
    out = await dispatch_staff_run(_cmd(), CALLER, barb_claim=_claim(lease["epoch"]))
    assert out["run"]["id"] in launches


@pytest.mark.unit
async def test_stale_epoch_dispatch_admits_nothing(
    store: BarbContinuityStore,
    staff: runner_mod.StaffRunner,  # noqa: F811
    launches: list[str],
) -> None:
    lease = store.checkout_lease(scope=SCOPE, holder="agent-alpha")["lease"]
    store.checkout_lease(scope=SCOPE, holder="op-1", force=True)
    with pytest.raises(HTTPException) as err:
        await dispatch_staff_run(_cmd(), CALLER, barb_claim=_claim(lease["epoch"]))
    assert err.value.status_code == 409
    assert err.value.detail["error"] == "stale_barb_lease"
    assert err.value.detail["current_epoch"] == lease["epoch"] + 1
    assert launches == []
    assert staff.store.list_runs(limit=5) == []


@pytest.mark.unit
async def test_takeover_between_check_and_admit_is_blocked_by_the_lock(
    store: BarbContinuityStore,
    staff: runner_mod.StaffRunner,  # noqa: F811
    launches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fire a takeover from another thread while ``admit`` runs: it must wait, so admission is consistent."""
    import threading  # noqa: PLC0415

    lease = store.checkout_lease(scope=SCOPE, holder="agent-alpha")["lease"]
    real_admit = staff.admit
    taker_done = threading.Event()

    def admit(req: Any, **kwargs: Any) -> Any:
        t = threading.Thread(
            target=lambda: (store.checkout_lease(scope=SCOPE, holder="op-1", force=True), taker_done.set())
        )
        t.start()
        assert not taker_done.wait(0.3), "takeover landed inside the check+admit critical section"
        return real_admit(req, **kwargs)

    monkeypatch.setattr(staff, "admit", admit)
    out = await dispatch_staff_run(_cmd(), CALLER, barb_claim=_claim(lease["epoch"]))
    assert out["run"]["id"] in launches
    assert taker_done.wait(5)  # takeover proceeds right after the write


@pytest.mark.unit
async def test_dispatch_without_claim_is_unaffected(
    store: BarbContinuityStore,
    staff: runner_mod.StaffRunner,  # noqa: F811
    launches: list[str],
) -> None:
    store.checkout_lease(scope=SCOPE, holder="op-1")
    out = await dispatch_staff_run(_cmd(), CALLER)
    assert out["run"]["id"] in launches


@pytest.mark.unit
async def test_fenced_dispatch_audit_trail_records_the_rejection(
    store: BarbContinuityStore,
    staff: runner_mod.StaffRunner,  # noqa: F811
    launches: list[str],
) -> None:
    """A fenced dispatch must not leave an ``admitted`` row standing as its final word (#2970)."""
    lease = store.checkout_lease(scope=SCOPE, holder="agent-alpha")["lease"]
    store.checkout_lease(scope=SCOPE, holder="op-1", force=True)
    with pytest.raises(HTTPException):
        await dispatch_staff_run(_cmd(), CALLER, barb_claim=_claim(lease["epoch"]))
    entries = get_audit_store().list_entries(action="dispatch")
    by_run: dict[str, list[str]] = {}
    for entry in entries:
        by_run.setdefault(entry.run_id, []).append(entry.outcome)
    assert len(by_run) == 1
    (outcomes,) = by_run.values()
    assert "rejected" in outcomes
    assert staff.store.list_runs(limit=5) == []
    assert launches == []


@pytest.mark.unit
async def test_forwarded_dispatch_enforces_barb_write_fence_and_propagates_headers(
    store: BarbContinuityStore,
    staff: runner_mod.StaffRunner,  # noqa: F811
    peers: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #3543: _forward() must pass barb_write_fence (409 on stale/missing) and propagate X-Barb-* headers."""
    from staff import fleet as fleet_mod  # noqa: PLC0415
    from staff.barb_lease_fence import claim_for_write  # noqa: PLC0415

    forwarded_calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    async def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        forwarded_calls.append((url, body, dict(headers)))
        return 200, {"dry_run": False, "run": {"id": "peer-run-1", "status": "queued"}, "machine": "node-b"}

    monkeypatch.setattr(fleet_mod, "post_json", fake_post)
    barb_caller = Principal(
        id="agent-grok",
        type="bot",
        name="Barb",
        roles=["bot"],
        scopes=["staff.dispatch"],
        assistant_kind="grokbot",
    )
    forward_cmd = DispatchCommand(role="ad-hoc", requested_by="agent-grok", prompt="hi", machine="node-b")

    # 1. Barb seat without X-Barb-* headers -> 409 claim_required, peer never called
    with pytest.raises(HTTPException) as missing_err:
        claim_none = claim_for_write({}, barb_caller, surface="staff.dispatch")
        await dispatch_staff_run(forward_cmd, barb_caller, barb_claim=claim_none)
    assert missing_err.value.status_code == 409
    assert missing_err.value.detail["reason"] == "claim_required"
    assert forwarded_calls == []

    # 2. Stale epoch on _forward() -> 409 stale_epoch / holder_mismatch, peer never called
    lease = store.checkout_lease(scope=SCOPE, holder="agent-grok")["lease"]
    store.checkout_lease(scope=SCOPE, holder="op-1", force=True)
    with pytest.raises(HTTPException) as stale_err:
        await dispatch_staff_run(
            forward_cmd,
            barb_caller,
            barb_claim=_claim(lease["epoch"], caller="agent-grok"),
        )
    assert stale_err.value.status_code == 409
    assert stale_err.value.detail["error"] == "stale_barb_lease"
    assert forwarded_calls == []

    # 3. Valid lease on _forward() -> 200 and X-Barb-* headers propagated to peer
    valid_lease = store.checkout_lease(scope=SCOPE, holder="agent-grok", force=True)["lease"]
    valid_headers = {
        "x-barb-scope": SCOPE,
        "x-barb-lease-epoch": str(valid_lease["epoch"]),
        "x-barb-holder": "agent-grok",
    }
    valid_claim = claim_for_write(valid_headers, barb_caller, surface="staff.dispatch")
    res = await dispatch_staff_run(forward_cmd, barb_caller, barb_claim=valid_claim)
    assert res["forwarded_to"] == "node-b"
    assert len(forwarded_calls) == 1
    _url, _body, sent_headers = forwarded_calls[0]
    assert sent_headers["X-Barb-Scope"] == SCOPE
    assert sent_headers["X-Barb-Lease-Epoch"] == str(valid_lease["epoch"])
    assert sent_headers["X-Barb-Holder"] == "agent-grok"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/staff/ad-hoc/run", {"prompt": "hi", "machine": "node-b"}),
        (
            "POST",
            "/api/v1/memory",
            {
                "entry_type": "session_digest",
                "source_product": "grokbot",
                "body": {"summary": "x", "open_items": []},
            },
        ),
        (
            "POST",
            "/api/v1/memory/session-digest",
            {
                "source_product": "codex",
                "conversation_id": "c-3543",
                "user_message_id": "u-3543",
                "summary": "x",
            },
        ),
        ("POST", "/api/v1/staff/barb/turnover", {"scope": SCOPE, "summary": "shift over"}),
        ("POST", "/api/v1/staff/barb/inventory", {"title": "task-3543", "scope": SCOPE}),
        (
            "PUT",
            "/api/priorities/directives",
            {"directives": [{"text": "ship", "repo": "*", "priority": 2}]},
        ),
    ],
)
def test_enumerated_write_routes_via_forward_and_fenced_surfaces_require_valid_barb_lease(
    method: str,
    path: str,
    body: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store: BarbContinuityStore,
    staff: runner_mod.StaffRunner,  # noqa: F811
    peers: dict[str, str],  # noqa: F811
) -> None:
    """Issue #3543: enumerate write routes via forward path without X-Barb headers (409) and with valid lease."""
    import hashlib  # noqa: PLC0415

    import orchestration_audit  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from identity import identity_manager  # noqa: PLC0415
    from memory.store import reset_memory_store  # noqa: PLC0415
    from staff import fleet as fleet_mod  # noqa: PLC0415

    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(tmp_path / "memory.sqlite3"))
    monkeypatch.setenv("STAFF_AUDIT_DB", str(tmp_path / "staff_audit.sqlite3"))
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "directives.json"))
    monkeypatch.delenv("DASHBOARD_BARB_FENCE_MODE", raising=False)
    monkeypatch.setattr(orchestration_audit, "ORCHESTRATION_AUDIT_PATH", tmp_path / "audit.ndjson")
    reset_memory_store()

    async def fake_post(url: str, req_body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        return 200, {"dry_run": False, "run": {"id": "peer-run-3543", "status": "queued"}, "machine": "node-b"}

    monkeypatch.setattr(fleet_mod, "post_json", fake_post)

    barb_prin = Principal(
        id="agent-grok",
        type="bot",
        name="Barb",
        roles=["bot"],
        scopes=[
            "staff.dispatch",
            "staff.read",
            "memory.read",
            "memory.write",
            "memory.shared.summarize",
            "priorities.write",
        ],
        assistant_kind="grokbot",
    )
    identity_manager.add_ephemeral_token(
        hashlib.sha256(b"tok-3543-barb").hexdigest(),
        barb_prin,
        name="barb-3543",
    )
    from server import app  # noqa: PLC0415

    client = TestClient(app, raise_server_exceptions=False, client=("100.64.0.35", 52035))
    base_headers = {"X-Requested-With": "XMLHttpRequest", "Authorization": "Bearer tok-3543-barb"}

    # Without X-Barb-* headers -> 409
    unclaimed = client.request(method, path, headers=base_headers, json=body)
    assert unclaimed.status_code == 409, f"{method} {path}: {unclaimed.status_code} {unclaimed.text}"

    # With valid lease -> 200 / 201
    lease = store.checkout_lease(scope=SCOPE, holder="agent-grok", force=True)["lease"]
    fenced_headers = {
        **base_headers,
        "X-Barb-Scope": SCOPE,
        "X-Barb-Lease-Epoch": str(lease["epoch"]),
    }
    claimed = client.request(method, path, headers=fenced_headers, json=body)
    assert claimed.status_code in (200, 201), f"{method} {path}: {claimed.status_code} {claimed.text}"
