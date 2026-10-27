from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, principal_has_scope, require_scope
from routers import staff_dispatcher
from staff import idempotency
from staff.dispatcher_metrics import DispatcherRateLimitError, record_dispatcher_fallback
from staff.store import RunRecord, RunStore

READER = Principal(id="reader", type="human", name="Reader", roles=[], scopes=["staff.read"])
WRITER = Principal(id="agent-grok", type="bot", name="Grok", roles=[], scopes=["staff.dispatcher.ledger.write"])


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    store = RunStore(tmp_path / "runs.sqlite3")
    monkeypatch.setattr("staff.runner.get_store", lambda: store)
    monkeypatch.setattr(staff_dispatcher, "get_runner", lambda: type("R", (), {"store": store})())
    monkeypatch.setattr(idempotency, "_GLOBAL_IDEMPOTENCY_STORE", idempotency.IdempotencyStore(tmp_path / "idem.db"))
    app = FastAPI()
    app.include_router(staff_dispatcher.router, prefix="/api/v1/staff")
    app.dependency_overrides[require_scope("staff.read")] = lambda: READER
    app.dependency_overrides[require_scope("staff.dispatcher.ledger.write")] = lambda: WRITER
    with TestClient(app) as test_client:
        test_client.store = store  # type: ignore[attr-defined]
        yield test_client


@pytest.mark.unit
def test_metrics_aggregate_dispatches_and_fallbacks_by_day(client: TestClient) -> None:
    store: RunStore = client.store  # type: ignore[attr-defined]
    today = datetime.now(UTC).date().isoformat()
    timestamp = f"{today}T12:00:00Z"
    store.create_run(
        RunRecord(
            id="grok-run",
            role="worker",
            provider="codex",
            model=None,
            machine="node",
            repo="repo",
            target_kind="issue",
            target_ref="1",
            prompt="",
            requested_by="principal:agent-grok",
            created_at=timestamp,
            status="succeeded",
            verification="verified",
        )
    )
    store.create_run(
        RunRecord(
            id="manual-run",
            role="worker",
            provider="codex",
            model=None,
            machine="node",
            repo="repo",
            target_kind="issue",
            target_ref="2",
            prompt="",
            requested_by="principal:human",
            created_at=timestamp,
            status="succeeded",
            verification="unverified",
        )
    )
    saved_grok_run = store.get_run("grok-run")
    assert saved_grok_run is not None
    assert saved_grok_run.dispatched_by == "principal:agent-grok"
    assert saved_grok_run.dispatch_origin == "grok"
    record_dispatcher_fallback(
        store.path,
        reason="hub_unreachable",
        task_class="small",
        est_grok_turns=3,
        created_at=timestamp,
        principal="principal:agent-grok",
        idempotency_key="fallback-1",
        rate_window_since=f"{today}T00:00:00Z",
    )

    response = client.get("/api/v1/staff/dispatcher/metrics", params={"days": 1})

    assert response.status_code == 200, response.text
    day = response.json()["days"][0]
    assert day["dispatch_ratio"] == pytest.approx(0.5)
    assert day["fallbacks_by_reason"] == {"hub_unreachable": 1}
    assert day["verified_success_rate"] == pytest.approx(0.5)
    assert day["false_success_count"] == 1
    assert day["estimated_grok_turns_avoided"] == {"value": 3, "estimate": True}


@pytest.mark.unit
def test_ledger_is_idempotent_and_rejects_unknown_reason(client: TestClient) -> None:
    headers = {"Idempotency-Key": "fallback-1"}
    body = {"reason": "held", "task_class": "small", "est_grok_turns": 2}

    first = client.post("/api/v1/staff/dispatcher/ledger", json=body, headers=headers)
    replay = client.post("/api/v1/staff/dispatcher/ledger", json=body, headers=headers)
    invalid = client.post(
        "/api/v1/staff/dispatcher/ledger",
        json={**body, "reason": "unknown"},
        headers={"Idempotency-Key": "fallback-2"},
    )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert invalid.status_code == 422


@pytest.mark.unit
def test_write_only_ledger_scope_cannot_read_metrics(client: TestClient) -> None:
    assert principal_has_scope(WRITER, "staff.dispatcher.ledger.write")
    assert not principal_has_scope(WRITER, "staff.read")


@pytest.mark.unit
def test_empty_metrics_days_are_zero_filled(client: TestClient) -> None:
    response = client.get("/api/v1/staff/dispatcher/metrics", params={"days": 2})

    assert response.status_code == 200, response.text
    assert [day["dispatch_ratio"] for day in response.json()["days"]] == [0.0, 0.0]
    assert [day["estimated_grok_turns_avoided"] for day in response.json()["days"]] == [
        {"value": 0, "estimate": True},
        {"value": 0, "estimate": True},
    ]


@pytest.mark.unit
def test_dispatcher_ledger_enforces_per_principal_rate_cap(client: TestClient) -> None:
    store: RunStore = client.store  # type: ignore[attr-defined]
    today = datetime.now(UTC).date().isoformat()
    for index in range(60):
        record_dispatcher_fallback(
            store.path,
            reason="held",
            task_class="small",
            est_grok_turns=1,
            created_at=f"{today}T12:00:00Z",
            principal="principal:agent-grok",
            idempotency_key=f"rate-{index}",
            rate_window_since=f"{today}T00:00:00Z",
        )

    with pytest.raises(DispatcherRateLimitError):
        record_dispatcher_fallback(
            store.path,
            reason="held",
            task_class="small",
            est_grok_turns=1,
            created_at=f"{today}T12:01:00Z",
            principal="principal:agent-grok",
            idempotency_key="rate-61",
            rate_window_since=f"{today}T00:00:00Z",
        )
