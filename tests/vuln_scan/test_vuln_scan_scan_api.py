"""``/api/v1/security/scans`` routes: scoped, default-branch only, dry run by default (#2572)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from projects import service as projects_service
from vuln_scan import findings as findings_mod
from vuln_scan import scan_service, scans
from vuln_scan.operator_config import ScanHost, parse_operator_config

SHA = "0bb24108aa" * 4


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    projects = tmp_path / "projects.json"
    projects.write_text(json.dumps({"repos": ["Tools"]}), encoding="utf-8")
    monkeypatch.setattr(projects_service, "_CONFIG_PATH", projects)
    cfg = tmp_path / "security_scan.json"
    cfg.write_text(json.dumps({"repos": {"Tools": {"enabled": True}}}), encoding="utf-8")
    monkeypatch.setenv("SECURITY_SCAN_CONFIG", str(cfg))
    monkeypatch.setenv("SECURITY_FINDINGS_DB_PATH", str(tmp_path / "findings.db"))
    monkeypatch.setattr(findings_mod, "_store", None)
    monkeypatch.setattr(scans, "_log", None)

    async def sha(repo: str) -> str:
        return SHA

    async def queued() -> int:
        return 0

    async def contract(repo: str) -> dict[str, Any]:
        return {"repo": repo, "valid": True}

    monkeypatch.setattr(scan_service, "default_branch_sha", sha)
    monkeypatch.setattr(scan_service, "queued_ci_jobs", queued)
    monkeypatch.setattr(scan_service.service, "contract_status", contract)
    monkeypatch.setattr(scan_service, "oauth_ok", lambda: True)
    monkeypatch.setattr(scan_service, "claude_config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(scan_service, "claude_cli_version", lambda cli: "2.1.263 (Claude Code)")
    import dashboard_config

    monkeypatch.setattr(dashboard_config, "MACHINE_ROLE", "hub")
    yield


def _client(monkeypatch: pytest.MonkeyPatch, roles: list[str]) -> TestClient:
    from routers import security_scan

    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(security_scan.router)
    app.dependency_overrides[require_principal] = lambda: Principal(id="tester", type="human", name="T", roles=roles)
    return TestClient(app, raise_server_exceptions=False)


def test_scan_routes_need_scopes(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    viewer = _client(monkeypatch, ["viewer"])
    assert viewer.post("/api/v1/security/scans", json={"repo": "Tools"}).status_code == 403
    assert viewer.get("/api/v1/security/scans").status_code == 403


def test_dry_run_is_the_default_and_reports_reasons(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    admin = _client(monkeypatch, ["admin"])
    resp = admin.post("/api/v1/security/scans", json={"repo": "Tools"})
    assert resp.status_code == 200, resp.text
    scan = resp.json()["scan"]
    assert scan["state"] == "planned" and scan["dry_run"] and scan["sha"] == SHA
    # The shipped posture: no VM host and no CLI pin, so a real scan could not start.
    assert any("no scan host" in r for r in scan["reasons"])
    assert any("not pinned" in r for r in scan["reasons"])
    listed = admin.get("/api/v1/security/scans").json()
    assert listed["count"] == 1
    assert admin.get(f"/api/v1/security/scans/{scan['id']}").json()["scan"]["id"] == scan["id"]


def test_real_request_without_vm_is_refused_and_starts_nothing(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    admin = _client(monkeypatch, ["admin"])
    scan = admin.post("/api/v1/security/scans", json={"repo": "Tools", "dry_run": False}).json()["scan"]
    assert scan["state"] == "refused" and scan_service._active == {}
    assert scan_service.scan_log().slot_free()


def test_request_cannot_name_a_sha_or_unknown_repo(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    admin = _client(monkeypatch, ["admin"])
    resp = admin.post("/api/v1/security/scans", json={"repo": "Tools", "sha": "1" * 40})
    assert resp.json()["scan"]["sha"] == SHA
    assert admin.post("/api/v1/security/scans", json={"repo": "Nope"}).status_code == 404
    assert admin.get("/api/v1/security/scans/not-an-id").status_code == 404
    assert admin.post(f"/api/v1/security/scans/{'a' * 16}/cancel").status_code == 404


def test_ssh_free_gb_parses_df(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def runner(argv: Any, timeout: float, cap: int, env: Any) -> Any:
        seen.append(list(argv))
        out = "Filesystem 1024-blocks Used Available Capacity Mounted\n/dev/sda1 1 1 104857600 5% /\n"
        return SimpleNamespace(exit_code=0, stdout=out)

    monkeypatch.setattr(scan_service, "subprocess_runner", runner)
    host = ScanHost("scanvm", "vm", "ssh://scan@scanvm:2222", "vs-build")
    assert scan_service.ssh_free_gb(host) == 100.0
    assert seen[0][-4:] == ["scan@scanvm", "df", "-Pk", "/var/lib/docker"]
    assert "-p" in seen[0] and "BatchMode=yes" in seen[0]
    assert scan_service.ssh_free_gb(ScanHost("x", "vm", None)) is None


def test_operator_config_parses_executor_and_build_network() -> None:
    config = parse_operator_config(
        {
            "scan_hosts": {"vm": {"isolation": "vm", "docker_host": "ssh://s@vm", "build_network": "vs-build"}},
            "executor": {"cli_version": "2.1.263", "max_turns": 50},
        }
    )
    assert config.errors == ()
    assert config.scan_hosts[0].build_network == "vs-build"
    assert config.executor.cli_version == "2.1.263" and config.executor.max_turns == 50
    bad = parse_operator_config(
        {
            "scan_hosts": {"vm": {"isolation": "vm", "build_network": "bad net;rm"}},
            "executor": {"cli_version": "latest", "build_timeout_minutes": 90, "model": "Bad Model"},
        }
    )
    assert len(bad.errors) == 4 and bad.executor.cli_version is None


def test_real_scans_are_refused_off_the_hub(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import dashboard_config

    monkeypatch.setattr(dashboard_config, "MACHINE_ROLE", "node")
    admin = _client(monkeypatch, ["admin"])
    resp = admin.post("/api/v1/security/scans", json={"repo": "Tools", "dry_run": False})
    assert resp.status_code == 409 and "hub" in resp.json()["detail"]
    assert admin.post("/api/v1/security/scans", json={"repo": "Tools"}).status_code == 200  # dry run


NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


def _queue(complete: bool = True, age_s: float = 10, count: Any = 0) -> dict[str, Any]:
    generated = (NOW - timedelta(seconds=age_s)).isoformat()
    return {"queued_jobs_count": count, "generated_at": generated, "stats": {"complete": complete}}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (_queue(), 0),
        (_queue(count=3), 3),
        (_queue(complete=False), None),  # a cached partial sample can miss a queue
        (_queue(age_s=3600), None),  # the stale fallback can be hours old
        (_queue(age_s=-600), None),
        (_queue(count="7"), None),
        ({"queued_jobs_count": 0, "stats": {"complete": True}}, None),
    ],
)
def test_queue_count_trusts_only_complete_fresh_payloads(payload: dict[str, Any], expected: Any) -> None:
    assert scan_service.queue_count(payload, NOW) == expected
