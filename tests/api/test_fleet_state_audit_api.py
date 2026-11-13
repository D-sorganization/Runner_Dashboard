"""API tests for GET /api/v1/fleet/state-audit."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    path = tmp_path / "rd-barb-state.md"
    path.write_text(
        "\n".join(
            [
                "# rd-barb live state",
                "",
                "| item | owner | source | check | verified_at (PT) | ttl |",
                "| --- | --- | --- | --- | --- | --- |",
                "| RD sample | rd-barb | RD #1 | `gh pr view 1` | 2026-11-09 12:00 | 1h |",
                "",
                "## Ledger",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from identity import require_fleet_peer
    from server import app  # noqa: PLC0415

    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    try:
        yield TestClient(app, raise_server_exceptions=False, headers={"X-Requested-With": "XMLHttpRequest"})
    finally:
        app.dependency_overrides.pop(require_fleet_peer, None)


def test_state_audit_rejects_noncanonical_path(client: TestClient) -> None:
    resp = client.get("/api/v1/fleet/state-audit", params={"path": "backend/server.py"})
    assert resp.status_code == 400


def test_state_audit_endpoint_returns_report(
    client: TestClient, state_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = {"path": str(state_file), "live_enabled": False, "tally": {"rows": 1}}
    monkeypatch.setattr(
        "routers.fleet_state_audit_route.resolve_canonical_state_path",
        lambda _path: state_file,
    )
    monkeypatch.setattr("routers.fleet_state_audit_route.run_audit", lambda *a, **k: sample)
    resp = client.get("/api/v1/fleet/state-audit", params={"live": "false"})
    assert resp.status_code == 200
    assert resp.json()["tally"]["rows"] == 1


def test_state_audit_returns_degraded_report_when_run_audit_raises(
    client: TestClient, state_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fallback = {
        "path": str(state_file),
        "live_enabled": False,
        "tally": {"rows": 1, "live_failures": 1, "live_checked": 1},
        "live": [],
    }
    calls = {"n": 0}

    def flaky_run(*_a: object, **kw: object) -> dict:
        calls["n"] += 1
        if kw.get("live") is True:
            raise RuntimeError("simulated upstream failure")
        return fallback

    monkeypatch.setattr(
        "routers.fleet_state_audit_route.resolve_canonical_state_path",
        lambda _path: state_file,
    )
    monkeypatch.setattr("routers.fleet_state_audit_route.run_audit", flaky_run)
    resp = client.get("/api/v1/fleet/state-audit", params={"live": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("live_degraded") is True
    assert any(p.get("problems") == ["live_checks_unavailable"] for p in body.get("live", []))
    assert calls["n"] >= 2


def test_state_audit_supports_cache_headers(
    client: TestClient, state_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "routers.fleet_state_audit_route.resolve_canonical_state_path",
        lambda _path: state_file,
    )
    monkeypatch.setattr(
        "routers.fleet_state_audit_route.run_audit",
        lambda *a, **k: {"path": str(state_file), "live_enabled": True, "tally": {"rows": 0}},
    )
    first = client.get("/api/v1/fleet/state-audit")
    etag = first.headers.get("etag")
    assert etag
    second = client.get("/api/v1/fleet/state-audit", headers={"If-None-Match": etag})
    assert second.status_code == 304
