"""Branch lookup failures cannot authorize cancellation (#3031)."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
import queue_cleanup as qc


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ((0, "{}", ""), True),
        ((1, "", "gh: Not Found (HTTP 404)"), False),
        ((1, "", "timeout"), None),
        ((1, "", "gh: rate limited (HTTP 429)"), None),
        ((1, "", "gh: Server Error (HTTP 503)"), None),
    ],
)
def test_branch_exists_api_error(monkeypatch, result, expected):
    monkeypatch.setattr(qc, "_gh", AsyncMock(return_value=result))
    assert asyncio.run(qc.branch_exists("org", "repo", "feature")) is expected


def test_branch_lookup_encodes_ref(monkeypatch):
    gh = AsyncMock(return_value=(0, "{}", ""))
    monkeypatch.setattr(qc, "_gh", gh)
    asyncio.run(qc.branch_exists("org", "repo", "feature/a#b?c%"))
    assert gh.call_args.args == ("api", "/repos/org/repo/branches/feature%2Fa%23b%3Fc%25")


@pytest.mark.parametrize("fork", [False, True])
def test_unknown_branch_never_makes_run_safe(monkeypatch, fork):
    run = {
        "id": 1,
        "name": "CI",
        "head_branch": "feature",
        "status": "queued",
        "created_at": "2026-01-01T00:00:00Z",
        "event": "pull_request",
        "pull_requests": [{"number": 8}],
        "head_sha": "old",
    }
    head = {"sha": "new", "ref": "feature", "repo": {"full_name": "fork/repo" if fork else "org/repo"}}

    async def gh_json(*args, **kwargs):
        if "status=queued" in args[1]:
            return {"workflow_runs": [run]}
        if "status=in_progress" in args[1]:
            return {"workflow_runs": []}
        if "/pulls/8" in args[1]:
            return {"state": "open", "head": head}
        raise AssertionError(args)

    gh = AsyncMock(return_value=(1, "", "timeout"))
    monkeypatch.setattr(qc, "_gh_json", gh_json)
    monkeypatch.setattr(qc, "_gh", gh)
    stale = asyncio.run(qc._queued_stale_for_repo("org", "repo", timedelta(0), []))
    assert all(not item.safe_to_cancel for item in stale)
    assert all(item.reason != qc.StaleReason.CLOSED_OR_DELETED_REF for item in stale)
    expected_owner = "fork" if fork else "org"
    assert gh.call_args.args[1] == f"/repos/{expected_owner}/repo/branches/feature"
