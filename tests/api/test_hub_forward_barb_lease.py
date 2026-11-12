"""Hub→peer staff dispatch must not re-validate Barb lease against the peer store (#Desk deploy)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal
from staff import adapters as adapters_mod
from staff import barb_continuity
from staff import fleet as fleet_mod
from staff import runner as runner_mod
from staff import runner_registry
from staff import store as store_mod
from staff.barb_continuity import BarbContinuityStore
from staff.barb_lease_fence import SCOPE_HEADER, EPOCH_HEADER, HOLDER_HEADER
from staff.dispatch_auth import require_staff_run_dispatch

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_SCOPE = "barb:dispatch-coordinator"
_TOKEN = "test-fleet-token"  # pragma: allowlist secret


@pytest.fixture
def barb_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BarbContinuityStore:
    store = BarbContinuityStore(tmp_path / "barb.sqlite3")
    monkeypatch.setattr(barb_continuity, "_DEFAULT_STORE", store)
    return store


@pytest.fixture
def staff_peer_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, runner_mod.StaffRunner]]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "no-roles"))
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(tmp_path / "wt"))
    monkeypatch.setenv("STAFF_HOLDS_FILE", str(tmp_path / "holds.json"))
    monkeypatch.setenv("HUB_FLEET_TOKEN", _TOKEN)
    script = tmp_path / "fake_cli.py"
    script.write_text('import sys; print(\'{"type": "result", "result": "ok"}\')\n', encoding="utf-8")
    fake = adapters_mod.ProviderAdapter(
        provider_id="claude",
        label="Fake claude",
        executable=sys.executable,
        argv=(str(script), "{prompt}"),
        json_lines=True,
    )
    adapters = {**adapters_mod.ADAPTERS, "claude": fake}
    store_mod.reset_store()
    runner_mod.reset_runner()
    runner = runner_mod.StaffRunner(
        store=store_mod.RunStore(tmp_path / "runs.sqlite3"),
        adapters=adapters,
        machine="node-b",
    )
    monkeypatch.setattr(runner_registry, "_runner", runner)
    monkeypatch.setattr(runner, "launch", lambda rec, plan: None)
    from routers import staff as staff_router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(staff_router.router)
    with TestClient(app) as client:
        yield client, runner
    store_mod.reset_store()
    runner_mod.reset_runner()


def _peer_dispatch_headers(
    *,
    obo_principal: str = "agent-grok",
    scope: str = _SCOPE,
    epoch: int = 99,
    holder: str = "agent-grok",
    fleet_bearer: bool = True,
) -> dict[str, str]:
    obo = fleet_mod.sign_on_behalf_of(obo_principal, surface="api", secret=_TOKEN)
    headers = {
        **_XHR,
        "X-Staff-On-Behalf-Of": obo,
        SCOPE_HEADER: scope,
        EPOCH_HEADER: str(epoch),
        HOLDER_HEADER: holder,
    }
    if fleet_bearer:
        headers["Authorization"] = f"Bearer {_TOKEN}"
    return headers


@pytest.mark.unit
def test_forwarded_dispatch_accepts_origin_validated_lease_without_peer_store(
    staff_peer_app: tuple[TestClient, runner_mod.StaffRunner],
    barb_store: BarbContinuityStore,
) -> None:
    client, _runner = staff_peer_app
    # Peer hub has no matching lease (origin validated on Desk before forward).
    barb_store.checkout_lease(scope=_SCOPE, holder="someone-else")

    client.app.dependency_overrides[require_staff_run_dispatch()] = lambda: Principal(
        id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"]
    )
    resp = client.post(
        "/api/staff/ad-hoc/run",
        json={"prompt": "cross-host", "machine": "local", "dry_run": True},
        headers=_peer_dispatch_headers(epoch=1),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["dry_run"] is True


@pytest.mark.unit
def test_direct_dispatch_with_stale_lease_still_409(
    staff_peer_app: tuple[TestClient, runner_mod.StaffRunner],
    barb_store: BarbContinuityStore,
) -> None:
    client, _runner = staff_peer_app
    lease = barb_store.checkout_lease(scope=_SCOPE, holder="agent-grok")["lease"]
    barb_store.checkout_lease(scope=_SCOPE, holder="op-2", force=True)

    client.app.dependency_overrides[require_staff_run_dispatch()] = lambda: Principal(
        id="agent-grok",
        type="bot",
        name="Barb",
        roles=["bot"],
        scopes=["staff.dispatch"],
        assistant_kind="grokbot",
    )
    resp = client.post(
        "/api/staff/ad-hoc/run",
            json={"prompt": "local stale", "machine": "local", "dry_run": False, "provider": "claude"},
        headers={
            **_XHR,
            SCOPE_HEADER: _SCOPE,
            EPOCH_HEADER: str(lease["epoch"]),
            HOLDER_HEADER: "agent-grok",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "stale_barb_lease"


@pytest.mark.unit
def test_unauthenticated_forward_with_barb_headers_still_fenced_locally(
    staff_peer_app: tuple[TestClient, runner_mod.StaffRunner],
    barb_store: BarbContinuityStore,
) -> None:
    client, _runner = staff_peer_app
    # Non-peer caller cannot use OBO; declared Barb lease must match this hub's store.
    barb_store.checkout_lease(scope=_SCOPE, holder="agent-grok")

    client.app.dependency_overrides[require_staff_run_dispatch()] = lambda: Principal(
        id="agent-grok",
        type="bot",
        name="Barb",
        roles=["bot"],
        scopes=["staff.dispatch"],
        assistant_kind="grokbot",
    )
    resp = client.post(
        "/api/staff/ad-hoc/run",
            json={"prompt": "spoofed forward", "machine": "local", "dry_run": False, "provider": "claude"},
        headers=_peer_dispatch_headers(epoch=999, fleet_bearer=False),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "stale_barb_lease"
