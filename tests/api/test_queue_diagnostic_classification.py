"""Wait-reason classification on GET /api/queue/diagnose (#2897)."""

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


def _run(repo: str, run_id: int) -> dict[str, Any]:
    return {
        "id": run_id,
        "status": "queued",
        "name": "Frontend Tests",
        "created_at": "2026-10-25T10:00:00Z",
        "repository": {"name": repo},
    }


def _runner(*, busy: bool = False, labels: list[str]) -> dict[str, Any]:
    return {
        "id": 1,
        "name": "fleet-1",
        "status": "online",
        "busy": busy,
        "labels": [{"name": label} for label in labels],
    }


def _job(name: str, labels: list[str]) -> dict[str, Any]:
    return {"id": 1, "name": name, "status": "queued", "labels": labels}


@pytest.fixture
def diagnostics_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from routers import queue_diagnostics as diag  # noqa: PLC0415

    monkeypatch.setattr(diag, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(diag, "cache_set", lambda *a, **k: None)

    app = FastAPI()
    app.include_router(diag.router)
    yield TestClient(app, raise_server_exceptions=False)


def test_diagnose_includes_wait_reason_classification_for_public_heavy_mismatch(
    diagnostics_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import queue_diagnostics as diag  # noqa: PLC0415

    async def fake_queue_impl() -> dict[str, Any]:
        return {"queued": [_run("Runner_Dashboard", 10)], "in_progress": [], "queued_jobs_count": 2}

    async def fake_fetch_org_runners(_api: Any, _org: str) -> dict[str, Any]:
        return {
            "runners": [
                _runner(
                    busy=False,
                    labels=["self-hosted", "Linux", "X64", "d-sorg-fleet"],
                )
            ]
        }

    async def fake_gh_api_admin(endpoint: str) -> dict[str, Any]:
        if "/jobs" in endpoint:
            return {
                "jobs": [
                    _job("Playwright E2E smoke tests", ["d-sorg-fleet-public-heavy"]),
                    _job("frontend-required", ["d-sorg-fleet"]),
                ]
            }
        if "runner-groups" in endpoint:
            return {"runner_groups": [{"name": "d-sorg-fleet-public-heavy", "visibility": "selected"}]}
        raise AssertionError(endpoint)

    monkeypatch.setattr(diag, "_queue_impl", fake_queue_impl)
    monkeypatch.setattr(diag, "fetch_org_runners", fake_fetch_org_runners)
    monkeypatch.setattr(diag, "gh_api_admin", fake_gh_api_admin)

    data = diagnostics_client.get("/api/queue/diagnose").json()

    classification = data.get("wait_reason_classification")
    assert classification is not None
    counts = classification["classification_counts"]
    assert counts["label_mismatch"] == 1
    assert counts["waiting_for_runner"] == 1
    assert sum(counts.values()) == classification["classification_job_count"]
    assert classification["f5_reserved_merge_group_label"]["proceed"] is False
    policy = data.get("workflow_label_policy")
    assert policy["group_label"] == "d-sorg-fleet-public-heavy"
    assert policy["online_runners_with_label"] == 0


def test_diagnose_records_runner_group_api_failure_in_classification(
    diagnostics_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import queue_diagnostics as diag  # noqa: PLC0415

    async def fake_queue_impl() -> dict[str, Any]:
        return {"queued": [_run("Runner_Dashboard", 10)], "in_progress": [], "queued_jobs_count": 1}

    async def fake_fetch_org_runners(_api: Any, _org: str) -> dict[str, Any]:
        return {"runners": [_runner(labels=["self-hosted", "Linux", "X64", "d-sorg-fleet"])]}

    async def fake_gh_api_admin(endpoint: str) -> dict[str, Any]:
        if "/jobs" in endpoint:
            return {"jobs": [_job("job", ["d-sorg-fleet"])]}
        if "runner-groups" in endpoint:
            raise RuntimeError("403 Resource not accessible by integration")
        raise AssertionError(endpoint)

    monkeypatch.setattr(diag, "_queue_impl", fake_queue_impl)
    monkeypatch.setattr(diag, "fetch_org_runners", fake_fetch_org_runners)
    monkeypatch.setattr(diag, "gh_api_admin", fake_gh_api_admin)

    classification = diagnostics_client.get("/api/queue/diagnose").json()["wait_reason_classification"]
    assert any("403" in item or "selected_repositories" in item for item in classification.get("api_findings", []))
