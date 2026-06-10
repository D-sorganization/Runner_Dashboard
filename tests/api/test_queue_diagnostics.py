from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")


def _run(repo: str, run_id: int, status: str = "queued") -> dict[str, Any]:
    return {
        "id": run_id,
        "status": status,
        "name": "ci",
        "created_at": "2026-06-10T10:00:00Z",
        "repository": {"name": repo},
    }


def _runner(
    name: str,
    *,
    status: str = "online",
    busy: bool = False,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": abs(hash(name)) % 100000,
        "name": name,
        "status": status,
        "busy": busy,
        "labels": [{"name": label} for label in (labels or ["self-hosted", "Linux", "X64"])],
    }


def _job(name: str, labels: list[str]) -> dict[str, Any]:
    return {
        "id": abs(hash(name)) % 100000,
        "name": name,
        "status": "queued",
        "labels": labels,
        "html_url": f"https://github.invalid/jobs/{name}",
    }


@pytest.fixture
def diagnostics_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from routers import queue_diagnostics as diag  # noqa: PLC0415

    monkeypatch.setattr(diag, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(diag, "cache_set", lambda *a, **k: None)

    app = FastAPI()
    app.include_router(diag.router)
    yield TestClient(app, raise_server_exceptions=False)


def test_diagnose_reports_runner_pool_and_queued_job_targets(
    diagnostics_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import queue_diagnostics as diag  # noqa: PLC0415

    async def fake_queue_impl() -> dict[str, Any]:
        return {
            "queued": [_run("RepoA", 10)],
            "in_progress": [_run("RepoA", 20, "in_progress")],
            "queued_count": 1,
            "queued_jobs_count": 2,
        }

    async def fake_fetch_org_runners(_api: Any, _org: str) -> dict[str, Any]:
        return {
            "total_count": 3,
            "runners": [
                _runner(
                    "fast-io-1",
                    busy=True,
                    labels=["self-hosted", "Linux", "X64", "d-sorg-fleet", "d-sorg-fleet-fast-io"],
                ),
                _runner("generic-1", busy=False),
                _runner("offline-1", status="offline"),
            ],
        }

    async def fake_gh_api_admin(endpoint: str) -> dict[str, Any]:
        if "/10/jobs" in endpoint:
            return {"jobs": [_job("backend", ["self-hosted", "Linux", "X64", "d-sorg-fleet-fast-io"])]}
        if "/20/jobs" in endpoint:
            return {"jobs": [_job("lint", ["self-hosted", "Linux", "X64"])]}
        raise AssertionError(endpoint)

    monkeypatch.setattr(diag, "_queue_impl", fake_queue_impl)
    monkeypatch.setattr(diag, "fetch_org_runners", fake_fetch_org_runners)
    monkeypatch.setattr(diag, "gh_api_admin", fake_gh_api_admin)

    response = diagnostics_client.get("/api/queue/diagnose")

    assert response.status_code == 200
    data = response.json()
    assert data["runner_pool"] == {"total": 3, "online": 2, "busy": 1, "idle": 1, "offline": 1}
    assert data["queued_runs_found"] == 1
    assert data["queued_jobs_count"] == 2
    assert data["jobs_sampled"] == 2
    assert data["waiting_for_fleet"] == 1
    assert data["waiting_for_generic_self_hosted"] == 1
    assert data["waiting_for_self_hosted"] == 2
    assert data["waiting_for_github_hosted"] == 0
    assert data["pick_runner_misconfig"] == []
    assert data["sampled_jobs"][0]["target"] == "self-hosted (d-sorg-fleet)"
    assert "self-hosted, Linux, X64, d-sorg-fleet-fast-io" in data["label_breakdown"]


def test_diagnose_flags_unroutable_self_hosted_labels(
    diagnostics_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import queue_diagnostics as diag  # noqa: PLC0415

    async def fake_queue_impl() -> dict[str, Any]:
        return {"queued": [_run("RepoA", 10)], "in_progress": [], "queued_jobs_count": 1}

    async def fake_fetch_org_runners(_api: Any, _org: str) -> dict[str, Any]:
        return {"runners": [_runner("linux-1", labels=["self-hosted", "Linux", "X64"])]}

    async def fake_gh_api_admin(_endpoint: str) -> dict[str, Any]:
        return {"jobs": [_job("gpu-test", ["self-hosted", "Linux", "X64", "d-sorg-fleet-gpu"])]}

    monkeypatch.setattr(diag, "_queue_impl", fake_queue_impl)
    monkeypatch.setattr(diag, "fetch_org_runners", fake_fetch_org_runners)
    monkeypatch.setattr(diag, "gh_api_admin", fake_gh_api_admin)

    data = diagnostics_client.get("/api/queue/diagnose").json()

    assert data["waiting_for_fleet"] == 1
    assert data["pick_runner_misconfig"][0]["missing_labels"] == ["d-sorg-fleet-gpu"]
    assert "no online runner can satisfy" in data["bottleneck"]


def test_diagnose_treats_fleet_label_without_self_hosted_as_self_hosted(
    diagnostics_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import queue_diagnostics as diag  # noqa: PLC0415

    async def fake_queue_impl() -> dict[str, Any]:
        return {"queued": [_run("RepoA", 10)], "in_progress": [], "queued_jobs_count": 1}

    async def fake_fetch_org_runners(_api: Any, _org: str) -> dict[str, Any]:
        return {
            "runners": [
                _runner(
                    "docker-1",
                    labels=["self-hosted", "Linux", "X64", "d-sorg-fleet", "d-sorg-fleet-docker"],
                )
            ]
        }

    async def fake_gh_api_admin(_endpoint: str) -> dict[str, Any]:
        return {"jobs": [_job("docker-test", ["d-sorg-fleet-docker"])]}

    monkeypatch.setattr(diag, "_queue_impl", fake_queue_impl)
    monkeypatch.setattr(diag, "fetch_org_runners", fake_fetch_org_runners)
    monkeypatch.setattr(diag, "gh_api_admin", fake_gh_api_admin)

    data = diagnostics_client.get("/api/queue/diagnose").json()

    assert data["waiting_for_fleet"] == 1
    assert data["waiting_for_github_hosted"] == 0
    assert data["pick_runner_misconfig"] == []
    assert data["sampled_jobs"][0]["target"] == "self-hosted (d-sorg-fleet)"


def test_diagnose_degrades_when_runner_inventory_fails(
    diagnostics_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import queue_diagnostics as diag  # noqa: PLC0415

    async def fake_queue_impl() -> dict[str, Any]:
        return {"queued": [_run("RepoA", 10)], "in_progress": [], "queued_jobs_count": 1}

    async def fake_fetch_org_runners(_api: Any, _org: str) -> dict[str, Any]:
        raise RuntimeError("runner api down")

    async def fake_gh_api_admin(_endpoint: str) -> dict[str, Any]:
        return {"jobs": [_job("hosted", ["ubuntu-latest"])]}

    monkeypatch.setattr(diag, "_queue_impl", fake_queue_impl)
    monkeypatch.setattr(diag, "fetch_org_runners", fake_fetch_org_runners)
    monkeypatch.setattr(diag, "gh_api_admin", fake_gh_api_admin)

    response = diagnostics_client.get("/api/queue/diagnose")

    assert response.status_code == 200
    data = response.json()
    assert data["runner_pool"] == {"total": 0, "online": 0, "busy": 0, "idle": 0, "offline": 0}
    assert data["waiting_for_github_hosted"] == 1
    assert data["errors"]["runner_inventory"] == "runner api down"


def test_diagnose_uses_cache_without_fetching(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import queue_diagnostics as diag  # noqa: PLC0415

    cached = {
        "runner_pool": {"total": 9, "online": 9, "busy": 0, "idle": 9, "offline": 0},
        "bottleneck": "cached",
    }
    monkeypatch.setattr(diag, "cache_get", lambda *a, **k: cached)

    async def fail_queue_impl() -> dict[str, Any]:
        raise AssertionError("cache hit should not fetch queue")

    monkeypatch.setattr(diag, "_queue_impl", fail_queue_impl)

    app = FastAPI()
    app.include_router(diag.router)
    response = TestClient(app, raise_server_exceptions=False).get("/api/queue/diagnose")

    assert response.status_code == 200
    assert response.json()["bottleneck"] == "cached"
