"""Security posture service and ``/api/v1/security`` routes (#2567, #2569, #2570).

GitHub is faked at ``gh_utils.gh_api`` (resolved lazily by ``vuln_scan.service``);
the findings store is a temp SQLite file.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from cache_utils import cache_clear
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from projects import service as projects_service
from vuln_scan import findings as findings_mod
from vuln_scan import github_alerts
from vuln_scan.findings import validate_finding

ORG = "D-sorganization"
PROJECT = "repo: https://github.com/D-sorganization/Alpha\nprimary_contact: sec@example.org\n"
FINDING = {
    "title": "XXE in URDF import",
    "cwe": "CWE-611",
    "severity": "high",
    "confidence": "high",
    "path": "src/urdf.py",
    "symbol": "parse",
    "root_cause": "external entities enabled",
}


def _contents(text: str) -> dict[str, Any]:
    raw = text.encode("utf-8")
    return {"content": base64.b64encode(raw).decode("ascii"), "size": len(raw)}


def fake_github(*, alerts_forbidden: bool = False) -> Any:
    calls: list[str] = []

    async def fake(endpoint: str) -> Any:
        calls.append(endpoint)
        if endpoint == f"/repos/{ORG}/Alpha/contents/.oss-scanner/project.yaml":
            return _contents(PROJECT)
        if endpoint == f"/repos/{ORG}/Alpha/contents/.oss-scanner/Dockerfile":
            return {"size": 120}
        if endpoint == f"/repos/{ORG}/Gamma/contents/.oss-scanner/project.yaml":
            raise HTTPException(status_code=502, detail="boom")
        if "/dependabot/alerts" in endpoint:
            if alerts_forbidden:
                raise HTTPException(status_code=403, detail="forbidden")
            return [{"security_advisory": {"severity": "moderate"}}, {"security_advisory": {"severity": "high"}}]
        if "/code-scanning/alerts" in endpoint:
            return [{"rule": {"security_severity_level": "critical"}}]
        if "/secret-scanning/alerts" in endpoint:
            raise HTTPException(status_code=404, detail="disabled")
        raise HTTPException(status_code=404, detail=f"not found: {endpoint}")

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Any]:
    projects = tmp_path / "projects.json"
    projects.write_text(json.dumps({"repos": ["Alpha", "Beta", "Gamma"]}), encoding="utf-8")
    monkeypatch.setattr(projects_service, "_CONFIG_PATH", projects)
    cfg = tmp_path / "security_scan.json"
    cfg.write_text(json.dumps({"repos": {"Alpha": {"enabled": True}}}), encoding="utf-8")
    monkeypatch.setenv("SECURITY_SCAN_CONFIG", str(cfg))
    monkeypatch.setenv("SECURITY_FINDINGS_DB_PATH", str(tmp_path / "findings.db"))
    monkeypatch.setattr(findings_mod, "_store", None)
    fake = fake_github()
    import gh_utils

    monkeypatch.setattr(gh_utils, "gh_api", fake)
    cache_clear()
    yield fake
    cache_clear()


def _client(monkeypatch: pytest.MonkeyPatch, roles: list[str]) -> TestClient:
    from routers import security_scan

    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(security_scan.router)
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="tester", type="human", name="Tester", roles=roles
    )
    return TestClient(app, raise_server_exceptions=False)


async def test_alert_counts_map_severity_and_unavailable() -> None:
    counts = await github_alerts.alert_counts(ORG, "Alpha", fake_github())
    assert counts["dependabot"] == {
        "status": "ok",
        "open": 2,
        "by_severity": {"medium": 1, "high": 1},
        "capped": False,
    }
    assert counts["code_scanning"]["by_severity"] == {"critical": 1}
    assert counts["secret_scanning"]["status"] == "unavailable"
    forbidden = await github_alerts.alert_counts(ORG, "Alpha", fake_github(alerts_forbidden=True))
    assert forbidden["dependabot"] == {"status": "unavailable", "reason": "HTTP 403"}


def test_contracts_route_reports_each_repo_without_5xx(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    body = _client(monkeypatch, ["viewer"]).get("/api/v1/security/contracts").json()
    by_repo = {c["repo"]: c for c in body["contracts"]}
    assert by_repo["Alpha"]["valid"] is True
    assert by_repo["Beta"]["present"] is False
    assert by_repo["Gamma"]["error"] == "GitHub HTTP 502"
    assert body["summary"] == {"repos": 3, "enrolled": 1, "valid": 1, "invalid": 0, "errors": 1}


def test_contract_reads_are_cached(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, ["viewer"])
    client.get("/api/v1/security/contracts/Alpha")
    first = len(env.calls)
    client.get("/api/v1/security/contracts/Alpha")
    assert len(env.calls) == first


def test_unknown_repo_is_404(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, ["viewer"])
    assert client.get("/api/v1/security/contracts/Nope").status_code == 404
    assert client.get("/api/v1/security/contracts/..%2Fetc").status_code == 404


def test_findings_and_posture_need_security_read(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    viewer = _client(monkeypatch, ["viewer"])
    for path in ("/api/v1/security/findings", "/api/v1/security/posture", "/api/v1/security/config"):
        assert viewer.get(path).status_code == 403, path


def test_posture_joins_contract_findings_and_alerts(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    findings_mod.get_findings_store().record("Alpha", validate_finding(FINDING), scan_sha="abcdef1")
    body = _client(monkeypatch, ["admin"]).get("/api/v1/security/posture").json()
    alpha = next(r for r in body["repos"] if r["repo"] == "Alpha")
    assert alpha["contract"]["valid"] and alpha["operator"]["enabled"]
    assert alpha["open_findings"] == {"high": 1}
    assert alpha["github_alerts"]["dependabot"]["open"] == 2
    assert body["operator"]["real_scans_possible"] is False


def test_triage_flow_is_scoped_validated_and_audited(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    fid, _ = findings_mod.get_findings_store().record("Alpha", validate_finding(FINDING), scan_sha="abcdef1")
    url = f"/api/v1/security/findings/{fid}/triage"
    assert _client(monkeypatch, ["viewer"]).post(url, json={"state": "confirmed"}).status_code == 403
    admin = _client(monkeypatch, ["admin"])
    assert admin.post(url, json={"state": "bogus"}).status_code == 422
    assert admin.post(url, json={"state": "false_positive"}).status_code == 422
    ok = admin.post(url, json={"state": "confirmed", "note": "reproduced"})
    assert ok.status_code == 200 and ok.json()["finding"]["state"] == "confirmed"
    detail = admin.get(f"/api/v1/security/findings/{fid}").json()
    assert detail["audit"][0]["actor"] == "tester"
    listed = admin.get("/api/v1/security/findings", params={"state": "confirmed"}).json()
    assert listed["count"] == 1
    assert admin.get("/api/v1/security/findings", params={"severity": "urgent"}).status_code == 422
    assert admin.get("/api/v1/security/findings/not-an-id").status_code == 404
    assert admin.post(f"/api/v1/security/findings/{'0' * 32}/triage", json={"state": "confirmed"}).status_code == 404


def test_advisory_route_is_scoped_gated_and_single(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import gh_utils

    calls: list[tuple[str, str, Any]] = []

    async def fake_write(endpoint: str, method: str = "POST", json_body: Any = None) -> dict[str, Any]:
        calls.append((endpoint, method, json_body))
        ghsa = "GHSA-2xq9-c4vm-wh7p"
        return {"ghsa_id": ghsa, "html_url": f"https://github.com/{ORG}/Alpha/security/advisories/{ghsa}"}

    monkeypatch.setattr(gh_utils, "gh_api_write", fake_write)
    store = findings_mod.get_findings_store()
    fid, _ = store.record("Alpha", validate_finding(FINDING), scan_sha="abcdef1")
    url = f"/api/v1/security/findings/{fid}/advisory"
    assert _client(monkeypatch, ["viewer"]).post(url).status_code == 403
    admin = _client(monkeypatch, ["admin"])
    assert admin.post(url).status_code == 422  # still new: not confirmed
    store.triage(fid, "confirmed", actor="verifier:claude-sonnet-5-5")
    ok = admin.post(url)
    assert ok.status_code == 200 and ok.json()["advisory"]["ghsa_id"] == "GHSA-2xq9-c4vm-wh7p"
    assert admin.post(url).status_code == 409
    assert len(calls) == 1 and calls[0][2]["start_private_fork"] is True
    detail = admin.get(f"/api/v1/security/findings/{fid}").json()
    assert detail["advisory"]["actor"] == "tester"
    assert detail["finding"]["patch_protected_paths"] == []


def test_sarif_export_is_opt_in(env: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    findings_mod.get_findings_store().record("Alpha", validate_finding(FINDING), scan_sha="abcdef1")
    admin = _client(monkeypatch, ["admin"])
    assert admin.get("/api/v1/security/export/sarif").status_code == 404
    cfg = tmp_path / "security_scan.json"
    cfg.write_text(json.dumps({"repos": {"Alpha": {"enabled": True}}, "sarif_export": True}), encoding="utf-8")
    response = admin.get("/api/v1/security/export/sarif", params={"repo": "Alpha"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/sarif+json")
    assert response.json()["runs"][0]["results"][0]["ruleId"] == "CWE-611"
    assert _client(monkeypatch, ["viewer"]).get("/api/v1/security/export/sarif").status_code == 403


def test_coverage_route_joins_contracts_settings_and_scans(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _client(monkeypatch, ["viewer"]).get("/api/v1/security/coverage").status_code == 403
    body = _client(monkeypatch, ["admin"]).get("/api/v1/security/coverage").json()
    by_repo = {r["repo"]: r for r in body["coverage"]}
    assert by_repo["Alpha"]["contract_valid"] and by_repo["Alpha"]["enabled"] and by_repo["Alpha"]["due"]
    assert by_repo["Gamma"]["contract_error"] == "GitHub HTTP 502"
    assert body["summary"]["due"] == 1
    assert body["schedule"] == {"enabled": False, "check_minutes": 30, "retry_hours": 20}
