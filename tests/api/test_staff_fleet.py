"""Hub fan-out board, summary and machine targeting (issues #1195 / #1197).

No network: ``staff.fleet.get_json`` / ``post_json`` are replaced with fakes and
``peer_nodes`` is monkeypatched, so the merge, placement and forwarding logic is
exercised deterministically.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from staff import adapters as adapters_mod
from staff import fleet as fleet_mod
from staff import runner as runner_mod
from staff import store as store_mod

_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _board(machine: str, running: int = 0, queued: int = 0, providers: dict[str, bool] | None = None) -> dict:
    return {
        "machine": machine,
        "running": [{"id": f"{machine}-r{i}", "role": "x", "status": "running"} for i in range(running)],
        "queued": [{"id": f"{machine}-q{i}", "role": "x", "status": "queued"} for i in range(queued)],
        "recent": [],
        "spend_today_usd": {"claude": 1.0, "total": 1.0},
        "providers": providers if providers is not None else {"claude": True, "codex": False},
    }


# ── pure fleet helpers ───────────────────────────────────────────────────
@pytest.mark.unit
def test_resolve_machine_handles_local_aliases_case_and_unknown() -> None:
    peers = {"OGLaptop": "http://og:8321", "ControlTower": "http://ct:8321"}
    assert fleet_mod.resolve_machine("local", "DeskComputer", peers) == "local"
    assert fleet_mod.resolve_machine("deskcomputer", "DeskComputer", peers) == "local"
    assert fleet_mod.resolve_machine("oglaptop", "DeskComputer", peers) == "OGLaptop"
    assert fleet_mod.resolve_machine("Mars", "DeskComputer", peers) is None


@pytest.mark.unit
def test_choose_machine_prefers_least_loaded_with_provider_and_ties_to_local() -> None:
    board = {
        "machines": {
            "Desk": {**_board("Desk", running=1), "status": "online"},
            "OG": {**_board("OG", running=0, providers={"claude": True}), "status": "online"},
            "CT": {**_board("CT", running=0, providers={"claude": False, "codex": True}), "status": "online"},
            "Dead": {"status": "offline"},
        }
    }
    assert fleet_mod.choose_machine(board, "claude", "Desk") == "OG"
    assert fleet_mod.choose_machine(board, "codex", "Desk") == "CT"
    board["machines"]["OG"]["running"] = board["machines"]["Desk"]["running"]
    assert fleet_mod.choose_machine(board, "claude", "Desk") == "Desk"  # tie → local
    assert fleet_mod.choose_machine(board, "gemini", "Desk") == "Desk"  # nothing qualifies → local


@pytest.mark.unit
async def test_aggregate_board_merges_online_peers_and_marks_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        assert headers["X-Requested-With"] == "XMLHttpRequest"
        if "og:" in url:
            return _board("OGLaptop", running=2, queued=1)
        raise ConnectionError("boom")

    monkeypatch.setattr(fleet_mod, "get_json", fake_get)
    merged = await fleet_mod.aggregate_board(
        _board("Desk", running=1), {"OGLaptop": "http://og:8321", "ControlTower": "http://ct:8321"}
    )
    assert merged["hub"] == "Desk"
    assert merged["online"] == ["Desk", "OGLaptop"] and merged["offline"] == ["ControlTower"]
    assert len(merged["running"]) == 3 and len(merged["queued"]) == 1
    assert merged["spend_today_usd"] == {"claude": 2.0, "total": 2.0}
    assert merged["machines"]["ControlTower"]["status"] == "offline"
    assert set(merged["providers"]) == {"Desk", "OGLaptop"}


# ── routes ───────────────────────────────────────────────────────────────
def test_board_reports_rm_revision_and_roles_alias(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import staff as staff_router

    expected = {"commit": "a" * 40, "status": "updated", "commit_age_seconds": 30, "check_age_seconds": 5}
    monkeypatch.setattr(staff_router, "source_status", lambda: expected)
    assert client.get("/api/staff/board?local=1").json()["rm_source"] == expected
    assert client.get("/api/staff/roles").json()["roles"] == client.get("/api/staff/roster").json()["roles"]


@pytest.fixture
def staff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[runner_mod.StaffRunner]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "no-roles"))
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(tmp_path / "wt"))
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
    r = runner_mod.StaffRunner(store=store_mod.RunStore(tmp_path / "runs.sqlite3"), adapters=adapters, machine="Desk")
    monkeypatch.setattr(runner_mod, "_runner", r)
    yield r
    store_mod.reset_store()
    runner_mod.reset_runner()


@pytest.fixture
def client(staff: runner_mod.StaffRunner) -> Iterator[TestClient]:
    from identity import require_fleet_peer, require_orchestrator_peer  # noqa: PLC0415
    from routers import staff as staff_router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(staff_router.router)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    app.dependency_overrides[require_orchestrator_peer] = lambda: "test-orchestrator"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def peers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    nodes = {"OGLaptop": "http://og:8321"}
    monkeypatch.setattr(fleet_mod, "peer_nodes", lambda: nodes)

    async def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        return _board("OGLaptop", running=0, providers={"claude": True})

    monkeypatch.setattr(fleet_mod, "get_json", fake_get)
    return nodes


@pytest.mark.unit
def test_board_is_local_without_peers_and_with_local_flag(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fleet_mod, "peer_nodes", lambda: {})
    body = client.get("/api/staff/board").json()
    assert body["machine"] == "Desk" and "machines" not in body
    monkeypatch.setattr(fleet_mod, "peer_nodes", lambda: {"OGLaptop": "http://og:8321"})
    assert "machines" not in client.get("/api/staff/board", params={"local": "true"}).json()


@pytest.mark.unit
def test_board_aggregates_with_peers(client: TestClient, peers: dict[str, str]) -> None:
    body = client.get("/api/staff/board").json()
    assert body["hub"] == "Desk"
    assert body["online"] == ["Desk", "OGLaptop"]
    assert body["machines"]["OGLaptop"]["status"] == "online"


@pytest.mark.unit
def test_summary_is_flat_and_complete(client: TestClient, peers: dict[str, str], staff: runner_mod.StaffRunner) -> None:
    rec = store_mod.RunRecord(
        id="run-bad", role="ad-hoc", provider="claude", model=None, machine="Desk", repo="Tools",
        target_kind="issue", target_ref="#7", prompt="x", status="failed", error="exit 3",
    )  # fmt: skip
    staff.store.create_run(rec)
    body = client.get("/api/staff/summary").json()
    for key in (
        "generated_at",
        "hub",
        "machines_online",
        "in_flight",
        "recent_24h",
        "attention",
        "spend_today_usd",
        "holds",
        "roles",
    ):
        assert key in body, key
    assert body["recent_24h"] == {"failed": 1}
    assert body["attention"][0]["id"] == "run-bad"
    assert body["holds"] == []
    assert any(r["name"] == "ad-hoc" for r in body["roles"])


@pytest.mark.unit
def test_dispatch_unknown_machine_is_422(client: TestClient, peers: dict[str, str]) -> None:
    resp = client.post("/api/staff/ad-hoc/run", json={"prompt": "x", "machine": "Mars", "dry_run": True}, headers=_XHR)
    assert resp.status_code == 422 and "unknown machine" in resp.json()["detail"]


@pytest.mark.unit
def test_dispatch_forwards_to_named_peer(
    client: TestClient, peers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        seen.update({"url": url, "body": body, "headers": headers})
        return 200, {"dry_run": True, "plan": {"provider": "claude"}, "machine": "OGLaptop"}

    monkeypatch.setattr(fleet_mod, "post_json", fake_post)
    resp = client.post(
        "/api/staff/ad-hoc/run", json={"prompt": "hi", "machine": "oglaptop", "dry_run": True}, headers=_XHR
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["forwarded_to"] == "OGLaptop"
    assert seen["url"] == "http://og:8321/api/staff/ad-hoc/run"
    assert seen["body"]["machine"] == "local" and seen["body"]["dry_run"] is True
    assert seen["headers"]["X-Requested-With"] == "XMLHttpRequest"


@pytest.mark.unit
def test_dispatch_auto_picks_idle_peer_and_falls_back_locally(
    client: TestClient, peers: dict[str, str], monkeypatch: pytest.MonkeyPatch, staff: runner_mod.StaffRunner
) -> None:
    # Local node has one running job; the peer is idle → auto forwards to the peer.
    busy = store_mod.RunRecord(
        id="run-busy", role="ad-hoc", provider="claude", model=None, machine="Desk", repo="",
        target_kind="prompt", target_ref="", prompt="x", status="running",
    )  # fmt: skip
    staff.store.create_run(busy)
    calls: list[str] = []

    async def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        calls.append(url)
        return 200, {"dry_run": True, "plan": {}, "machine": "OGLaptop"}

    monkeypatch.setattr(fleet_mod, "post_json", fake_post)
    resp = client.post("/api/staff/ad-hoc/run", json={"prompt": "hi", "machine": "auto", "dry_run": True}, headers=_XHR)
    assert resp.status_code == 200 and resp.json()["forwarded_to"] == "OGLaptop"
    assert calls == ["http://og:8321/api/staff/ad-hoc/run"]

    # Peer unreachable → auto stays local (degraded fleet still runs work).
    async def dead_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        raise ConnectionError("down")

    monkeypatch.setattr(fleet_mod, "get_json", dead_get)
    resp = client.post("/api/staff/ad-hoc/run", json={"prompt": "hi", "machine": "auto", "dry_run": True}, headers=_XHR)
    assert resp.status_code == 200 and resp.json()["machine"] == "Desk" and "forwarded_to" not in resp.json()


@pytest.mark.unit
def test_forward_errors_map_to_503_and_4xx(
    client: TestClient, peers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def dead_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        raise ConnectionError("down")

    monkeypatch.setattr(fleet_mod, "post_json", dead_post)
    resp = client.post("/api/staff/ad-hoc/run", json={"prompt": "hi", "machine": "OGLaptop"}, headers=_XHR)
    assert resp.status_code == 503

    async def rejecting_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        return 422, {"detail": "role scoped elsewhere"}

    monkeypatch.setattr(fleet_mod, "post_json", rejecting_post)
    resp = client.post("/api/staff/ad-hoc/run", json={"prompt": "hi", "machine": "OGLaptop"}, headers=_XHR)
    assert resp.status_code == 422 and resp.json()["detail"] == "role scoped elsewhere"
