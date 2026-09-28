"""Tests for backend/scheduled_workflows.py — issue #386, #1745."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import scheduled_workflows as sw

# ---------------------------------------------------------------------------
# extract_cron_expressions
# ---------------------------------------------------------------------------


SIMPLE_CRON_YAML = """\
name: Nightly CI
on:
  schedule:
    - cron: '0 2 * * *'
  push:
    branches: [main]
"""

MULTI_CRON_YAML = """\
name: Multi Schedule
on:
  schedule:
    - cron: '0 1 * * 1'
    - cron: '30 6 * * *'
"""

NO_CRON_YAML = """\
name: Push Only
on:
  push:
    branches: [main]
"""

CRON_WITH_COMMENT_YAML = """\
name: With Comment
on:
  schedule:
    - cron: '0 3 * * *'  # runs daily at 3am UTC
"""


def test_extract_cron_single() -> None:
    result = sw.extract_cron_expressions(SIMPLE_CRON_YAML)
    assert result == ["0 2 * * *"]


def test_extract_cron_multiple() -> None:
    result = sw.extract_cron_expressions(MULTI_CRON_YAML)
    assert "0 1 * * 1" in result
    assert "30 6 * * *" in result
    assert len(result) == 2


def test_extract_cron_no_schedule() -> None:
    result = sw.extract_cron_expressions(NO_CRON_YAML)
    assert result == []


def test_extract_cron_empty_string() -> None:
    result = sw.extract_cron_expressions("")
    assert result == []


def test_extract_cron_strips_inline_comment() -> None:
    result = sw.extract_cron_expressions(CRON_WITH_COMMENT_YAML)
    assert result == ["0 3 * * *"]


def test_extract_cron_deduplicates() -> None:
    yaml = """\
on:
  schedule:
    - cron: '0 1 * * *'
    - cron: '0 1 * * *'
"""
    result = sw.extract_cron_expressions(yaml)
    assert result.count("0 1 * * *") == 1


# ---------------------------------------------------------------------------
# collect_inventory — reads workflow files by blob SHA (cached), #1745
# ---------------------------------------------------------------------------


class _FakeGh:
    """Fake ``gh_json`` that records calls and serves a one-repo, two-workflow org."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.blob_calls: list[str] = []

    async def __call__(self, endpoint: str) -> Any:
        self.calls.append(endpoint)
        if endpoint.startswith("/orgs/"):
            return [{"name": "alpha", "archived": False, "default_branch": "main"}]
        if endpoint.endswith("/actions/workflows"):
            return {
                "workflows": [
                    {"id": 1, "name": "Nightly", "path": ".github/workflows/nightly.yml", "state": "active"},
                    {"id": 2, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"},
                ]
            }
        if endpoint.endswith("/contents/.github/workflows"):
            return [
                {"path": ".github/workflows/nightly.yml", "sha": "sha-nightly", "type": "file"},
                {"path": ".github/workflows/ci.yml", "sha": "sha-ci", "type": "file"},
            ]
        if "/git/blobs/" in endpoint:
            self.blob_calls.append(endpoint)
            sha = endpoint.rsplit("/", 1)[-1]
            content = SIMPLE_CRON_YAML if sha == "sha-nightly" else NO_CRON_YAML
            return {"content": base64.b64encode(content.encode()).decode()}
        if endpoint.startswith("/repos/org/alpha/actions/workflows/1/runs"):
            return {
                "workflow_runs": [
                    {
                        "id": 99,
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "https://example.invalid/run/99",
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:05:00Z",
                    }
                ]
            }
        raise AssertionError(f"unexpected endpoint: {endpoint}")


def test_collect_inventory_marks_scheduled_workflow_from_blob() -> None:
    sw._CRON_BY_BLOB_SHA.clear()
    fake = _FakeGh()
    report = asyncio.run(sw.collect_inventory("org", fake))
    repo = report.repositories[0]
    by_path = {wf.workflow_path: wf for wf in repo.workflows}
    assert by_path[".github/workflows/nightly.yml"].scheduled is True
    assert by_path[".github/workflows/nightly.yml"].cron_expressions == ("0 2 * * *",)
    assert by_path[".github/workflows/nightly.yml"].schedule_source != "unavailable"
    assert by_path[".github/workflows/ci.yml"].scheduled is False
    assert by_path[".github/workflows/nightly.yml"].latest_run is not None
    assert by_path[".github/workflows/nightly.yml"].latest_run.run_id == 99


def test_collect_inventory_fetches_each_blob_sha_once_across_calls() -> None:
    """A second walk with unchanged SHAs must not re-fetch any blob (#1745)."""
    sw._CRON_BY_BLOB_SHA.clear()
    fake = _FakeGh()
    asyncio.run(sw.collect_inventory("org", fake))
    first_blob_call_count = len(fake.blob_calls)
    assert first_blob_call_count == 2  # one per distinct workflow SHA

    asyncio.run(sw.collect_inventory("org", fake))
    assert len(fake.blob_calls) == first_blob_call_count  # zero new blob fetches


def test_collect_inventory_warm_cache_call_pattern_per_repo() -> None:
    """Warm cache: workflows list + contents listing + one runs call per scheduled workflow."""
    sw._CRON_BY_BLOB_SHA.clear()
    fake = _FakeGh()
    asyncio.run(sw.collect_inventory("org", fake))  # warm the blob cache
    fake.calls.clear()
    fake.blob_calls.clear()

    asyncio.run(sw.collect_inventory("org", fake))

    assert not fake.blob_calls
    # 1 org listing + (1 workflows list + 1 contents listing + 1 runs call for the
    # single scheduled workflow) for the one repo.
    assert len(fake.calls) == 4


def test_collect_inventory_marks_unavailable_when_listing_fails() -> None:
    sw._CRON_BY_BLOB_SHA.clear()

    async def flaky_gh(endpoint: str) -> Any:
        if endpoint.startswith("/orgs/"):
            return [{"name": "alpha", "archived": False, "default_branch": "main"}]
        if endpoint.endswith("/actions/workflows"):
            return {
                "workflows": [{"id": 1, "name": "Nightly", "path": ".github/workflows/nightly.yml", "state": "active"}]
            }
        if endpoint.endswith("/contents/.github/workflows"):
            raise RuntimeError("listing unavailable")
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    report = asyncio.run(sw.collect_inventory("org", flaky_gh))
    workflow = report.repositories[0].workflows[0]
    assert workflow.schedule_source == "unavailable"
    assert workflow.scheduled is False


def test_collect_inventory_walks_repos_with_bounded_concurrency_and_stable_order() -> None:
    sw._CRON_BY_BLOB_SHA.clear()
    repo_names = [f"repo{i}" for i in range(12)]
    in_flight = 0
    max_in_flight = 0

    async def fake_gh(endpoint: str) -> Any:
        nonlocal in_flight, max_in_flight
        if endpoint.startswith("/orgs/"):
            return [{"name": name, "archived": False, "default_branch": "main"} for name in repo_names]
        if endpoint.endswith("/actions/workflows"):
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1
            return {"workflows": []}
        if endpoint.endswith("/contents/.github/workflows"):
            return []
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    report = asyncio.run(sw.collect_inventory("org", fake_gh, repo_limit=100))

    assert [repo.repository for repo in report.repositories] == repo_names
    assert 2 <= max_in_flight <= sw._REPO_CONCURRENCY
