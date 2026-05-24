"""Tests for workflows.run_enrichment — GitHub run/job enrichment helpers.

All tests are fully offline. run_cmd is monkeypatched where needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import workflows.run_enrichment as re_mod  # noqa: E402
from workflows.run_enrichment import (  # noqa: E402
    EnrichedRun,
    Job,
    JobPlacement,
    Run,
    _enrich_run_with_job_placement,
    _fetch_failed_log_excerpt,
    _fetch_repo_runs,
    _fetch_run_jobs,
    _get_recent_org_repos,
    _placement_from_jobs,
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


def test_run_model_basic() -> None:
    run = Run(id=1, name="CI", status="completed", conclusion="success")
    assert run.id == 1


def test_job_model_basic() -> None:
    job = Job(id=10, name="build", status="completed", conclusion="success")
    assert job.id == 10


def test_job_placement_model() -> None:
    jp = JobPlacement(
        runner_id=1,
        runner_name="d-sorg-local-host-1",
        runner_group_name="Default",
        runner_labels=["self-hosted", "linux"],
        machine_name="host",
    )
    assert jp.machine_name == "host"


def test_enriched_run_model() -> None:
    er = EnrichedRun(id=5, name="Deploy", status="completed", conclusion="success", machine_name="myhost")
    assert er.machine_name == "myhost"


# ---------------------------------------------------------------------------
# _get_recent_org_repos (async)
# ---------------------------------------------------------------------------


async def test_get_recent_org_repos_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = [{"name": "repo-a"}, {"name": "repo-b"}]

    async def fake_run_cmd(cmd, timeout=20):  # noqa: ANN001, ARG001
        return 0, json.dumps(repos), ""

    monkeypatch.setattr(re_mod, "run_cmd", fake_run_cmd)
    result = await _get_recent_org_repos(limit=2)
    assert result == repos


async def test_get_recent_org_repos_empty_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_cmd(cmd, timeout=20):  # noqa: ANN001, ARG001
        return 1, "", "error"

    monkeypatch.setattr(re_mod, "run_cmd", fake_run_cmd)
    result = await _get_recent_org_repos()
    assert result == []


async def test_get_recent_org_repos_empty_on_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_cmd(cmd, timeout=20):  # noqa: ANN001, ARG001
        return 0, "not-json", ""

    monkeypatch.setattr(re_mod, "run_cmd", fake_run_cmd)
    result = await _get_recent_org_repos()
    assert result == []


# ---------------------------------------------------------------------------
# _fetch_repo_runs (async)
# ---------------------------------------------------------------------------


async def test_fetch_repo_runs_annotates_missing_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs without a 'repository' key get one injected."""
    payload = {"workflow_runs": [{"id": 1, "name": "CI"}]}

    async def fake_run_cmd(cmd, timeout=15):  # noqa: ANN001, ARG001
        return 0, json.dumps(payload), ""

    monkeypatch.setattr(re_mod, "run_cmd", fake_run_cmd)
    result = await _fetch_repo_runs("my-repo")
    assert len(result) == 1
    assert result[0]["repository"]["name"] == "my-repo"


async def test_fetch_repo_runs_preserves_existing_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs that already have a 'repository' key keep it."""
    payload = {"workflow_runs": [{"id": 2, "name": "CI", "repository": {"name": "original"}}]}

    async def fake_run_cmd(cmd, timeout=15):  # noqa: ANN001, ARG001
        return 0, json.dumps(payload), ""

    monkeypatch.setattr(re_mod, "run_cmd", fake_run_cmd)
    result = await _fetch_repo_runs("ignored-name")
    assert result[0]["repository"]["name"] == "original"


async def test_fetch_repo_runs_empty_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_cmd(cmd, timeout=15):  # noqa: ANN001, ARG001
        return 1, "", "error"

    monkeypatch.setattr(re_mod, "run_cmd", fake_run_cmd)
    result = await _fetch_repo_runs("my-repo")
    assert result == []


# ---------------------------------------------------------------------------
# _fetch_run_jobs (async)
# ---------------------------------------------------------------------------


async def test_fetch_run_jobs_returns_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"jobs": [{"id": 1, "name": "build", "runner_name": "d-sorg-local-host-1"}]}

    async def fake_run_cmd(cmd, timeout=10):  # noqa: ANN001, ARG001
        return 0, json.dumps(payload), ""

    monkeypatch.setattr(re_mod, "run_cmd", fake_run_cmd)
    result = await _fetch_run_jobs("my-repo", 123)
    assert len(result) == 1
    assert result[0]["name"] == "build"


async def test_fetch_run_jobs_empty_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_cmd(cmd, timeout=10):  # noqa: ANN001, ARG001
        return 1, "", "error"

    monkeypatch.setattr(re_mod, "run_cmd", fake_run_cmd)
    result = await _fetch_run_jobs("my-repo", 999)
    assert result == []


# ---------------------------------------------------------------------------
# _fetch_failed_log_excerpt (async)
# ---------------------------------------------------------------------------


async def test_fetch_failed_log_excerpt_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_cmd(cmd, timeout=20):  # noqa: ANN001, ARG001
        return 0, "Error: something went wrong", ""

    monkeypatch.setattr(re_mod, "run_cmd", fake_run_cmd)
    result = await _fetch_failed_log_excerpt("my-repo", 42)
    assert "something went wrong" in result


async def test_fetch_failed_log_excerpt_empty_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_cmd(cmd, timeout=20):  # noqa: ANN001, ARG001
        return 1, "", "error"

    monkeypatch.setattr(re_mod, "run_cmd", fake_run_cmd)
    result = await _fetch_failed_log_excerpt("my-repo", 42)
    assert result == ""


async def test_fetch_failed_log_excerpt_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Logs exceeding 12 000 chars are truncated."""
    long_text = "x" * 20000

    async def fake_run_cmd(cmd, timeout=20):  # noqa: ANN001, ARG001
        return 0, long_text, ""

    monkeypatch.setattr(re_mod, "run_cmd", fake_run_cmd)
    result = await _fetch_failed_log_excerpt("my-repo", 1)
    assert len(result) <= 12000


# ---------------------------------------------------------------------------
# _placement_from_jobs
# ---------------------------------------------------------------------------


def test_placement_from_jobs_extracts_runner_name() -> None:
    jobs = [
        {
            "runner_name": "d-sorg-local-myhost-2",
            "runner_id": 5,
            "runner_group_name": "Default",
            "labels": ["self-hosted"],
        }
    ]
    result = _placement_from_jobs(jobs)
    assert result["runner_name"] == "d-sorg-local-myhost-2"
    assert result["machine_name"] == "myhost"


def test_placement_from_jobs_empty_list() -> None:
    result = _placement_from_jobs([])
    assert result == {}


def test_placement_from_jobs_skips_jobs_without_runner_name() -> None:
    jobs = [
        {"runner_name": None},
        {"runner_name": "d-sorg-local-host-1", "runner_id": 3, "runner_group_name": "Default", "labels": []},
    ]
    result = _placement_from_jobs(jobs)
    assert result["runner_name"] == "d-sorg-local-host-1"


# ---------------------------------------------------------------------------
# _enrich_run_with_job_placement (async)
# ---------------------------------------------------------------------------


async def test_enrich_run_with_job_placement_adds_machine_name(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [{"runner_name": "d-sorg-local-myhost-3", "runner_id": 9, "runner_group_name": "Default", "labels": []}]

    async def fake_fetch_run_jobs(repo_name, run_id):  # noqa: ANN001, ARG001
        return jobs

    monkeypatch.setattr(re_mod, "_fetch_run_jobs", fake_fetch_run_jobs)
    run = {"id": 1, "name": "CI", "repository": {"name": "my-repo"}}
    result = await _enrich_run_with_job_placement(run)
    assert result["machine_name"] == "myhost"
    assert result["runner_name"] == "d-sorg-local-myhost-3"


async def test_enrich_run_no_repo_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run with no repository info gets machine_name='GitHub' fallback."""

    async def fake_fetch_run_jobs(repo_name, run_id):  # noqa: ANN001, ARG001
        return []

    monkeypatch.setattr(re_mod, "_fetch_run_jobs", fake_fetch_run_jobs)
    run = {"id": 2, "name": "CI"}
    result = await _enrich_run_with_job_placement(run)
    assert "machine_name" in result
