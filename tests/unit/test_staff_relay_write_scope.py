"""Run-card relay write scope and run ownership (RM#2856 / E-01)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from identity import Principal, require_fleet_peer, require_scope
from routers.staff_threads import router as staff_threads_router
from staff.conversations import ConversationStore
from staff.runner import get_runner
from staff.store import RunRecord
from staff.thread_bus import ThreadEventBus
from staff.tokens import mint_run_token

_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _relay_body(run_id: str) -> dict[str, str]:
    return {
        "run_id": run_id,
        "role": "librarian",
        "status": "running",
        "node": "nodeB",
        "provider": "claude",
    }


@pytest.fixture
def relay_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("HUB_FLEET_TOKEN", "relay-test-fleet-secret")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    from staff import runner as runner_mod
    from staff import store as store_mod

    store_mod.reset_store()
    runner_mod.reset_runner()
    conv = ConversationStore(tmp_path / "conv.sqlite3")
    thread = conv.create_thread(title="t", kind="direct", role="librarian", created_by="user:d")
    runner = get_runner()
    run = RunRecord(
        id="run-relay-own",
        role="librarian",
        provider="claude",
        model=None,
        machine="nodeB",
        repo="Repo",
        target_kind="prompt",
        target_ref="",
        prompt="p",
        thread_id=thread.id,
        status="running",
    )
    runner.store.create_run(run)

    app = FastAPI()
    app.include_router(staff_threads_router, prefix="/api/v1/staff")
    from routers import staff_threads

    staff_threads._get_store_or_503 = lambda: conv
    staff_threads.get_thread_bus = lambda: ThreadEventBus()

    return {"app": app, "conv": conv, "thread": thread, "run": run}


def test_relay_card_anonymous_is_401(relay_env: dict[str, Any]) -> None:
    client = TestClient(relay_env["app"], raise_server_exceptions=False)
    resp = client.post(
        f"/api/v1/staff/threads/{relay_env['thread'].id}/relay-card",
        json=_relay_body("run-relay-own"),
        headers=_XHR,
    )
    assert resp.status_code == 401


def test_relay_card_read_scope_is_403(relay_env: dict[str, Any]) -> None:
    app = relay_env["app"]
    app.dependency_overrides[require_fleet_peer] = lambda: "peer"

    def deny_relay_write() -> Principal:
        raise HTTPException(
            status_code=403,
            detail={"error": "Authorization failed", "required_scope": "staff.relay.write", "principal": "viewer"},
        )

    app.dependency_overrides[require_scope("staff.relay.write")] = deny_relay_write
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        f"/api/v1/staff/threads/{relay_env['thread'].id}/relay-card",
        json=_relay_body("run-relay-own"),
        headers=_XHR,
    )
    assert resp.status_code == 403


def test_relay_card_cross_run_is_403(relay_env: dict[str, Any]) -> None:
    fleet = Principal(id="fleet-peer", type="bot", name="Fleet", roles=["fleet-peer"])
    app = relay_env["app"]
    app.dependency_overrides[require_fleet_peer] = lambda: "peer"
    app.dependency_overrides[require_scope("staff.relay.write")] = lambda: fleet
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        f"/api/v1/staff/threads/{relay_env['thread'].id}/relay-card",
        json=_relay_body("other-run"),
        headers=_XHR,
    )
    assert resp.status_code == 403


def test_relay_card_owner_is_200(relay_env: dict[str, Any]) -> None:
    run = relay_env["run"]
    token = mint_run_token(run.role, run.id, (), ttl_seconds=600.0)
    from identity import identity_manager

    principal, reason = identity_manager.resolve_bearer_token(token)
    assert principal is not None and reason is None
    app = relay_env["app"]
    app.dependency_overrides[require_fleet_peer] = lambda: "peer"
    app.dependency_overrides[require_scope("staff.relay.write")] = lambda: principal
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        f"/api/v1/staff/threads/{relay_env['thread'].id}/relay-card",
        json=_relay_body(run.id),
        headers=_XHR,
    )
    assert resp.status_code == 200
