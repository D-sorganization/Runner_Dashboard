"""Integration tests for Remediation Issues and PRs bulk actions going through the work request API.

SC-G5-4 (Issue #1500):
1. RemediationIssues.tsx and RemediationPRs.tsx invoke the unified request API
   (POST /api/v1/staff/requests) with kinds `issue.act` and `pr.act`.
2. Old /api/issues/dispatch and /api/prs/dispatch endpoints are no longer called by
   remediation UI pages.
3. WorkRequest carries `force` and `approved_by` fields into dispatch executions.
4. Bulk requests generate a single work item listing all target numbers.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from server import app
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.thread_bus import reset_thread_bus
from staff.work_items import get_work_item_store, reset_work_item_store

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND_PAGES = _REPO_ROOT / "frontend" / "src" / "pages"
_XHR = {"X-Requested-With": "XMLHttpRequest"}
URL = "/api/v1/staff/requests"

OPERATOR = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator"],
    scopes=[
        "staff.read",
        "staff.chat",
        "staff.dispatch",
        "staff.approve",
        "remediation.dispatch",
        "workflows.dispatch",
    ],
)


@pytest.fixture(autouse=True)
def _stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    reset_conversation_store()
    reset_work_item_store()
    reset_thread_bus()
    app.dependency_overrides[require_principal] = lambda: OPERATOR
    yield
    app.dependency_overrides.clear()
    get_conversation_store().close()
    reset_conversation_store()
    reset_work_item_store()
    reset_thread_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def test_remediation_issues_tsx_uses_staff_requests_api() -> None:
    """RemediationIssues.tsx must use submitStaffRequest and buildBulkIssueRequest."""
    content = (_FRONTEND_PAGES / "RemediationIssues.tsx").read_text(encoding="utf-8")
    assert "/api/issues/dispatch" not in content, "RemediationIssues.tsx should not call legacy /api/issues/dispatch"
    assert "submitStaffRequest" in content
    assert "buildBulkIssueRequest" in content
    assert "formatBulkResponseResult" in content


def test_remediation_prs_tsx_uses_staff_requests_api() -> None:
    """RemediationPRs.tsx must use submitStaffRequest and buildBulkPRRequest."""
    content = (_FRONTEND_PAGES / "RemediationPRs.tsx").read_text(encoding="utf-8")
    assert "/api/prs/dispatch" not in content, "RemediationPRs.tsx should not call legacy /api/prs/dispatch"
    assert "submitStaffRequest" in content
    assert "buildBulkPRRequest" in content
    assert "formatBulkResponseResult" in content


def test_remediation_bulk_request_ts_exists_and_exports_helpers() -> None:
    """remediationBulkRequest.ts must export buildBulkIssueRequest, buildBulkPRRequest, and formatBulkResponseResult."""
    bulk_helper = _FRONTEND_PAGES / "Remediation" / "remediationBulkRequest.ts"
    assert bulk_helper.exists()
    content = bulk_helper.read_text(encoding="utf-8")
    assert "export function buildBulkIssueRequest" in content
    assert "export function buildBulkPRRequest" in content
    assert "export function formatBulkResponseResult" in content


def test_bulk_issue_act_creates_single_work_item_listing_all_targets(client: TestClient) -> None:
    """Submitting a bulk issue.act creates one work item listing all target issue numbers."""
    body = {
        "kind": "issue.act",
        "provider": "codex",
        "prompt": "Fix issues",
        "target": {"repo": "D-sorganization/Tools", "issues": [10, 20, 30]},
        "force": True,
        "approved_by": "dieter",
    }

    async def side_effect(_repo: str, num: int, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "dispatched", "envelope_id": f"env-{num}"}

    with patch("staff.work_request_executors._dispatch_issue_action", side_effect=side_effect):
        resp = client.post(URL, json=body, headers=_XHR)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["state"] == "executed"
    work_item_id = data["work_item_id"]
    assert work_item_id

    wi = get_work_item_store().get_work_item(work_item_id)
    assert wi is not None
    assert wi.title == "[issue.act] D-sorganization/Tools #10, #20, #30 Fix issues"
    assert wi.state == "open"


def test_bulk_pr_act_creates_single_work_item_listing_all_targets(client: TestClient) -> None:
    """Submitting a bulk pr.act creates one work item listing all target PR numbers."""
    body = {
        "kind": "pr.act",
        "provider": "claude",
        "prompt": "Review and fix PRs",
        "target": {"repo": "D-sorganization/Tools", "prs": [42, 43]},
        "approved_by": "dieter",
    }

    async def side_effect(_repo: str, num: int, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "dispatched", "envelope_id": f"env-{num}"}

    with patch("staff.work_request_executors._dispatch_pr_action", side_effect=side_effect):
        resp = client.post(URL, json=body, headers=_XHR)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["state"] == "executed"
    work_item_id = data["work_item_id"]
    assert work_item_id

    wi = get_work_item_store().get_work_item(work_item_id)
    assert wi is not None
    assert wi.title == "[pr.act] D-sorganization/Tools PR #42, PR #43 Review and fix PRs"
    assert wi.state == "open"
