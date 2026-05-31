"""API-level coverage for the Conductor orchestrator admission gate (issue #1282).

The Conductor orchestrator (in Repository_Management) calls these endpoints over
HTTP to obtain a CI dispatch slot before launching work, and to surface its
tracked queue on the dashboard's Conductor tab. Cross-repo traffic is HTTP-only;
nothing here imports from Repository_Management.

The whole surface is gated behind the ``DASHBOARD_ENABLE_CONDUCTOR`` feature flag
(default off) so it is inert until explicitly enabled — and so a Conductor-tab or
endpoint failure cannot cascade into other dashboard tabs (orthogonality).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

# The feature flag is read at request time (not import time), so no module
# reload is needed — flipping the env var is sufficient and keeps the router
# registered on the app intact.

# State-changing /api/* requests must carry the CSRF sentinel header (enforced
# by middleware.csrf_check). The Conductor tab sends it via fetch; the Conductor
# orchestrator (server-to-server) must send it too — it is a documented contract.
_XHR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def enabled_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient with the Conductor feature flag enabled and a fixed capacity."""
    monkeypatch.setenv("DASHBOARD_ENABLE_CONDUCTOR", "1")

    import orchestrator_api

    orchestrator_api.reset_state()

    from server import app

    client = TestClient(app, raise_server_exceptions=False)
    yield client
    orchestrator_api.reset_state()


@pytest.fixture
def disabled_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient with the Conductor feature flag explicitly disabled."""
    monkeypatch.setenv("DASHBOARD_ENABLE_CONDUCTOR", "0")

    import orchestrator_api

    orchestrator_api.reset_state()

    from server import app

    client = TestClient(app, raise_server_exceptions=False)
    yield client


def _set_capacity(idle: int, total: int = 10, busy: int = 0) -> None:
    """Override the injected capacity provider used by the orchestrator router."""
    import orchestrator_api

    def _provider() -> dict[str, int]:
        return {
            "idle_runners": idle,
            "online_runners": idle + busy,
            "busy_runners": busy,
            "total_runners": total,
        }

    orchestrator_api.set_capacity_provider(_provider)


# ─── Feature flag gating (orthogonality / reversibility) ────────────────────


def _lease(client: TestClient, **body: object):
    body.setdefault("requested_by", "conductor")
    return client.post("/api/orchestrator/lease", json=body, headers=_XHR)


def _release(client: TestClient, lease_id: str):
    return client.post("/api/orchestrator/release", json={"lease_id": lease_id}, headers=_XHR)


def _queue_action(client: TestClient, action: str):
    return client.post("/api/orchestrator/queue", json={"action": action}, headers=_XHR)


def test_lease_returns_404_when_flag_disabled(disabled_client: TestClient) -> None:
    resp = _lease(disabled_client, slots=1)
    assert resp.status_code == 404


def test_queue_get_returns_404_when_flag_disabled(disabled_client: TestClient) -> None:
    resp = disabled_client.get("/api/orchestrator/queue")
    assert resp.status_code == 404


# ─── Lease admission gate ───────────────────────────────────────────────────


def test_lease_granted_when_idle_capacity_above_reserve(enabled_client: TestClient) -> None:
    _set_capacity(idle=5)
    resp = _lease(enabled_client, slots=1, reserve=1)
    assert resp.status_code == 200
    body = resp.json()
    assert body["granted"] is True
    assert body["lease_id"]
    assert body["ttl_seconds"] > 0
    assert body["idle_runners"] == 5


def test_lease_denied_when_saturated(enabled_client: TestClient) -> None:
    # idle (1) - reserve (1) = 0 free slots -> deny (backpressure).
    _set_capacity(idle=1)
    resp = _lease(enabled_client, slots=1, reserve=1)
    assert resp.status_code == 200
    body = resp.json()
    assert body["granted"] is False
    assert body["lease_id"] is None
    assert body["reason"]


def test_lease_validation_rejects_zero_slots(enabled_client: TestClient) -> None:
    _set_capacity(idle=5)
    resp = _lease(enabled_client, slots=0)
    assert resp.status_code == 422


def test_granted_lease_consumes_capacity_for_next_request(enabled_client: TestClient) -> None:
    # Two free slots; first lease of 2 slots should exhaust them.
    _set_capacity(idle=3)
    first = _lease(enabled_client, slots=2, reserve=1)
    assert first.json()["granted"] is True
    second = _lease(enabled_client, slots=1, reserve=1)
    assert second.json()["granted"] is False


# ─── Release ────────────────────────────────────────────────────────────────


def test_release_frees_capacity(enabled_client: TestClient) -> None:
    _set_capacity(idle=3)
    granted = _lease(enabled_client, slots=2, reserve=1).json()
    lease_id = granted["lease_id"]

    rel = _release(enabled_client, lease_id)
    assert rel.status_code == 200
    assert rel.json()["released"] is True

    # Capacity is free again.
    again = _lease(enabled_client, slots=1, reserve=1)
    assert again.json()["granted"] is True


def test_release_unknown_lease_is_idempotent(enabled_client: TestClient) -> None:
    resp = _release(enabled_client, "does-not-exist")
    assert resp.status_code == 200
    assert resp.json()["released"] is False


# ─── Queue visibility + manual override ─────────────────────────────────────


def test_queue_get_reports_state(enabled_client: TestClient) -> None:
    _set_capacity(idle=4, busy=2, total=8)
    resp = enabled_client.get("/api/orchestrator/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "running"
    assert body["enabled"] is True
    assert "active_leases" in body
    assert body["capacity"]["idle_runners"] == 4


def test_queue_pause_blocks_new_leases(enabled_client: TestClient) -> None:
    _set_capacity(idle=5)
    paused = _queue_action(enabled_client, "pause")
    assert paused.status_code == 200
    assert paused.json()["mode"] == "paused"

    # While paused, leases are denied regardless of capacity (manual override).
    resp = _lease(enabled_client, slots=1, reserve=1)
    assert resp.json()["granted"] is False
    assert "paus" in resp.json()["reason"].lower()


def test_queue_resume_restores_leasing(enabled_client: TestClient) -> None:
    _set_capacity(idle=5)
    _queue_action(enabled_client, "pause")
    resumed = _queue_action(enabled_client, "resume")
    assert resumed.json()["mode"] == "running"

    resp = _lease(enabled_client, slots=1, reserve=1)
    assert resp.json()["granted"] is True


def test_queue_drain_denies_and_reports(enabled_client: TestClient) -> None:
    _set_capacity(idle=5)
    drained = _queue_action(enabled_client, "drain")
    assert drained.json()["mode"] == "draining"

    resp = _lease(enabled_client, slots=1, reserve=1)
    assert resp.json()["granted"] is False


def test_queue_action_validation_rejects_unknown(enabled_client: TestClient) -> None:
    resp = _queue_action(enabled_client, "explode")
    assert resp.status_code == 422


def test_queue_reports_work_provider_mix_and_budget(enabled_client: TestClient) -> None:
    _set_capacity(idle=5)
    enabled_client.post(
        "/api/orchestrator/lease",
        json={"requested_by": "conductor", "slots": 1, "reserve": 1, "provider": "claude_code_cli"},
        headers=_XHR,
    )
    body = enabled_client.get("/api/orchestrator/queue").json()
    assert body["work"]["active"] == 1
    assert body["work"]["blocked"] == 0
    assert body["provider_mix"]["claude_code_cli"] == 1
    assert "spent_usd" in body["budget"]
    assert "limit_usd" in body["budget"]


def test_blocked_work_reported_while_paused(enabled_client: TestClient) -> None:
    _set_capacity(idle=5)
    enabled_client.post(
        "/api/orchestrator/lease",
        json={"requested_by": "conductor", "slots": 1, "reserve": 1},
        headers=_XHR,
    )
    paused = _queue_action(enabled_client, "pause").json()
    assert paused["work"]["blocked"] == 1
