"""Strict external numeric boundaries for bounded job collection."""

from __future__ import annotations

from typing import Any

import gh_client
import job_metrics
import job_metrics_collector
import pytest
from job_metrics_payloads import positive_identifier


@pytest.mark.asyncio
async def test_skipped_record_reason_survives_capped_cycle(stats_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(path: str) -> Any:
        if path.startswith("/orgs/"):
            return [{"name": "alpha"}, {"name": "beta"}]
        if "/alpha/actions/runs?" in path:
            return {"workflow_runs": [{"id": "invalid"}]}
        if "/beta/actions/runs?" in path:
            return {"workflow_runs": []}
        raise AssertionError("invalid run ID must not reach jobs")

    monkeypatch.setattr(gh_client, "get", fake_get)
    first = await job_metrics_collector.collect_job_metrics("D-sorganization", max_repos=1)
    assert first["complete"] is False
    second = await job_metrics_collector.collect_job_metrics("D-sorganization", max_repos=1)
    assert second["traversal_complete"] is True
    assert second["complete"] is False
    assert "workflow_run_invalid_id" in second["partial_reasons"]


@pytest.fixture
def stats_db(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATS_DB_PATH", str(tmp_path / "stats.db"))


@pytest.mark.parametrize("value", [True, False, 1.5, "2", 0, -1])
def test_collection_caps_reject_non_positive_integers(value: Any) -> None:
    with pytest.raises(ValueError):
        job_metrics_collector._bounded_int("max_pages", value, 200)


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "7", None])
def test_positive_identifier_rejects_non_integer_values(value: Any) -> None:
    assert positive_identifier(value) is None


def test_positive_identifier_accepts_positive_integer() -> None:
    assert positive_identifier(7) == 7


@pytest.mark.parametrize("run_id", [True, False, 0, -1, 1.5, "21", None])
@pytest.mark.asyncio
async def test_invalid_run_id_is_reported_without_fetching_jobs(
    stats_db: None, monkeypatch: pytest.MonkeyPatch, run_id: Any
) -> None:
    async def fake_get(path: str) -> Any:
        if path.startswith("/orgs/"):
            return [{"name": "Runner_Dashboard"}]
        if "/actions/runs?" in path:
            assert "filter=" not in path
            return {"workflow_runs": [{"id": run_id}]}
        raise AssertionError("invalid run ID must not reach the jobs endpoint")

    monkeypatch.setattr(gh_client, "get", fake_get)
    result = await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=4)

    assert result["complete"] is False
    assert "workflow_run_invalid_id" in result["partial_reasons"]


@pytest.mark.parametrize("job_id", [True, False, 0, -1, 1.5, "7", None])
@pytest.mark.asyncio
async def test_invalid_job_id_is_partial_and_never_checkpointed(
    stats_db: None, monkeypatch: pytest.MonkeyPatch, job_id: Any
) -> None:
    async def fake_get(path: str) -> Any:
        if path.startswith("/orgs/"):
            return [{"name": "Runner_Dashboard"}]
        if "/actions/runs?" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 21,
                        "run_attempt": 1,
                        "status": "completed",
                        "conclusion": "success",
                        "updated_at": "2026-10-21T11:00:00Z",
                    }
                ]
            }
        if "/runs/21/jobs?" in path:
            assert "filter=all" in path
            return {"jobs": [{"id": job_id, "run_attempt": 1}]}
        raise AssertionError("unexpected endpoint")

    monkeypatch.setattr(gh_client, "get", fake_get)
    result = await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=4)

    assert result["complete"] is False
    assert "job_id_invalid" in result["partial_reasons"]
    with job_metrics.connect_stats_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == 0
        progress = conn.execute("SELECT collection_complete FROM workflow_job_runs").fetchone()
    assert progress["collection_complete"] == 0


@pytest.mark.parametrize(
    "malformed_fields",
    [
        {"labels": "linux"},
        {"labels": [None]},
        {"runner_name": {"name": "runner"}},
        {"runner_group_id": 1.5},
    ],
)
@pytest.mark.asyncio
async def test_malformed_job_metadata_is_partial_and_skipped(
    stats_db: None, monkeypatch: pytest.MonkeyPatch, malformed_fields: dict[str, Any]
) -> None:
    async def fake_get(path: str) -> Any:
        if path.startswith("/orgs/"):
            return [{"name": "Runner_Dashboard"}]
        if "/actions/runs?" in path:
            return {"workflow_runs": [{"id": 21, "run_attempt": 1, "updated_at": "v1"}]}
        if "/runs/21/jobs?" in path:
            return {"jobs": [{"id": 31, "run_attempt": 1, **malformed_fields}]}
        raise AssertionError("unexpected endpoint")

    monkeypatch.setattr(gh_client, "get", fake_get)
    result = await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=4)

    assert result["complete"] is False
    assert "job_payload_invalid" in result["partial_reasons"]
    with job_metrics.connect_stats_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == 0
        progress = conn.execute("SELECT collection_complete FROM workflow_job_runs").fetchone()
    assert progress["collection_complete"] == 0
