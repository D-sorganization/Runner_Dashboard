"""Standalone/no-fleet profile smoke test (#3180 slice 2).

The dashboard must boot and serve its core endpoints cleanly when no sibling
Repository_Management checkout is reachable, instead of erroring. This pins
that contract with a real TestClient boot (not just the integration_health.py
unit tests) so a regression in any fleet-dependent request path is caught.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        sys.platform == "win32" and not Path("/.dockerenv").exists(),
        reason=(
            "server.py module-import triggers security.py file-mode checks that "
            "fail on Windows-mounted filesystems; tests run on Linux CI."
        ),
    ),
]


@pytest.fixture
def no_rm_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No Repository_Management checkout reachable anywhere this process looks."""
    monkeypatch.delenv("STAFF_RM_ROOT", raising=False)
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path))


def test_health_endpoint_ok_without_rm(no_rm_env) -> None:
    from fastapi.testclient import TestClient
    from server import app

    resp = TestClient(app, raise_server_exceptions=False).get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_diagnostics_endpoint_ok_without_rm(no_rm_env, make_authed_client, viewer_principal) -> None:
    client = make_authed_client(viewer_principal)
    resp = client.get("/api/diagnostics")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["integrations"]["repository_management"]["available"] is False


def test_diagnostics_endpoint_reports_rm_available_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_authed_client, viewer_principal
) -> None:
    rm_dir = tmp_path / "Repository_Management"
    scripts_dir = rm_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "check_agent_claim.py").write_text("# marker\n")
    monkeypatch.setenv("STAFF_RM_ROOT", str(rm_dir))

    client = make_authed_client(viewer_principal)
    resp = client.get("/api/diagnostics")

    assert resp.status_code == 200
    assert resp.json()["integrations"]["repository_management"]["available"] is True
