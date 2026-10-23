from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")


async def _fleet_nodes() -> dict:
    return {"nodes": []}


async def _expected_version() -> str:
    return "4.9.16"


def _deployment_info() -> dict:
    return {"version": "4.9.16"}


def _deployment_state(nodes: list, expected: str) -> dict:
    return {"nodes": nodes, "expected": expected}


def test_deployment_router_uses_app_state_dependencies() -> None:
    from routers import deployment

    app = FastAPI()
    deployment.set_dependencies(
        app=app,
        get_fleet_nodes_impl=_fleet_nodes,
        deployment_info=_deployment_info,
        read_expected_dashboard_version=_expected_version,
        build_deployment_state=_deployment_state,
    )
    app.include_router(deployment.router)

    client = TestClient(app)

    assert client.get("/api/deployment").json() == {"version": "4.9.16"}
    assert client.get("/api/deployment/drift").json()["static_assets"]["status"] == "unavailable"
    assert client.get("/api/deployment/state").json() == {
        "nodes": [],
        "expected": "4.9.16",
    }


def test_backend_949_routers_have_no_optional_callable_globals() -> None:
    from routers import deployment, orchestration, orchestration_node_routes

    for module in (deployment, orchestration, orchestration_node_routes):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "Callable | None" not in source
        assert "global _" not in source


def test_deployment_drift_includes_read_only_static_asset_result(tmp_path: Path, monkeypatch) -> None:
    import hashlib

    from routers import deployment

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_bytes(b"dashboard")
    monkeypatch.setattr(deployment, "DEPLOYMENT_FILE", tmp_path / "deployment.json")

    def deployment_info() -> dict:
        return {
            "version": "4.9.16",
            "static_asset_manifest": {
                "schema": "runner-dashboard-static-assets-v1",
                "entries": [
                    {
                        "path": "index.html",
                        "size_bytes": 9,
                        "sha256": hashlib.sha256(b"dashboard").hexdigest(),
                    }
                ],
            },
        }

    app = FastAPI()
    deployment.set_dependencies(
        app=app,
        get_fleet_nodes_impl=_fleet_nodes,
        deployment_info=deployment_info,
        read_expected_dashboard_version=_expected_version,
        build_deployment_state=_deployment_state,
    )
    app.include_router(deployment.router)

    response = TestClient(app).get("/api/deployment/drift")

    assert response.status_code == 200
    assert response.json()["severity"] == "none"
    assert response.json()["static_assets"] == {
        "status": "match",
        "checked_files": 1,
        "changed": [],
        "missing": [],
    }
