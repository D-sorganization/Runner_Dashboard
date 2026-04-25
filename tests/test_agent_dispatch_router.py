"""Tests for backend/agent_dispatch_router.py (issue #82)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from agent_dispatch_router import (
    BulkDispatchResponse,
    DispatchItem,
    DispatchSelection,
    IssueDispatchRequest,
    PRDispatchRequest,
    dispatch_to_issues,
    dispatch_to_prs,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_run_cmd(returncode: int = 0, stdout: str = "", stderr: str = "") -> AsyncMock:
    async def _run(cmd: list[str], timeout: int = 30, cwd: Path | None = None) -> tuple[int, str, str]:
        return returncode, stdout, stderr

    return AsyncMock(side_effect=_run)


def _normalize(value: str) -> tuple[str, str]:
    if "/" in value:
        parts = value.split("/", 1)
        return parts[1], value
    return value, f"D-sorganization/{value}"


def _avail_patch(available: bool = True):
    detail = "ready" if available else "missing binary"
    status = "available" if available else "missing_binary"
    return patch(
        "agent_dispatch_router.agent_remediation.probe_provider_availability",
        return_value={
            "claude_code_cli": type(
                "A",
                (),
                {"available": available, "status": status, "detail": detail},
            )(),
        },
    )


async def _dispatch_prs(req: PRDispatchRequest, run_cmd_fn=None):
    if run_cmd_fn is None:
        run_cmd_fn = _make_run_cmd(0)
    with _avail_patch(True):
        return await dispatch_to_prs(
            req,
            run_cmd_fn=run_cmd_fn,
            org="D-sorganization",
            repo_root=Path("."),
            normalize_repository_fn=_normalize,
        )


async def _dispatch_issues(req: IssueDispatchRequest, run_cmd_fn=None, available: bool = True):
    if run_cmd_fn is None:
        run_cmd_fn = _make_run_cmd(0)
    with _avail_patch(available):
        return await dispatch_to_issues(
            req,
            run_cmd_fn=run_cmd_fn,
            org="D-sorganization",
            repo_root=Path("."),
            normalize_repository_fn=_normalize,
        )


# ─── PR dispatch tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_to_prs_single_happy_path() -> None:
    """dispatch_to_prs mode=single with mocked gh → 1 accepted, 0 rejected."""
    req = PRDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=76,
        ),
        provider="claude_code_cli",
        prompt="Address review comments",
    )
    result = await _dispatch_prs(req)
    assert isinstance(result, BulkDispatchResponse)
    assert result.accepted == 1
    assert result.rejected == []
    assert len(result.envelope_ids) == 1
    assert len(result.fingerprints) == 1


@pytest.mark.asyncio
async def test_dispatch_to_prs_all_past_cap_returns_error() -> None:
    """dispatch_to_prs mode=all with >100 items → error dict with status_code=400."""
    items = [DispatchItem(repository="D-sorganization/runner-dashboard", number=i) for i in range(1, 102)]
    req = PRDispatchRequest(
        selection=DispatchSelection(mode="all", items=items),
        provider="claude_code_cli",
        prompt="Review all PRs",
    )
    result = await _dispatch_prs(req)
    assert isinstance(result, dict)
    assert result.get("status_code") == 400
    assert "hard-cap" in result.get("error", "")


@pytest.mark.asyncio
async def test_dispatch_to_prs_provider_unavailable_returns_error() -> None:
    """dispatch_to_prs with unavailable provider → error dict with status_code=409."""
    req = PRDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=1,
        ),
        provider="claude_code_cli",
        prompt="Address review comments",
    )
    with _avail_patch(False):
        result = await dispatch_to_prs(
            req,
            run_cmd_fn=_make_run_cmd(0),
            org="D-sorganization",
            repo_root=Path("."),
            normalize_repository_fn=_normalize,
        )
    assert isinstance(result, dict)
    assert result.get("status_code") == 409


@pytest.mark.asyncio
async def test_dispatch_to_prs_gh_failure_populates_rejected() -> None:
    """When gh fails for a target, it appears in rejected[] with a reason."""
    req = PRDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=4,
        ),
        provider="claude_code_cli",
        prompt="Address review comments",
    )
    run_cmd_fn = _make_run_cmd(returncode=1, stderr="some gh error")
    result = await _dispatch_prs(req, run_cmd_fn=run_cmd_fn)
    assert isinstance(result, BulkDispatchResponse)
    assert result.accepted == 0
    assert len(result.rejected) == 1
    assert result.rejected[0]["number"] == 4
    assert result.rejected[0]["reason"]


# ─── Issue dispatch tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_to_issues_force_skips_pickability() -> None:
    """dispatch_to_issues force=True skips pickability and sets forced=true in audit."""
    req = IssueDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=10,
        ),
        provider="claude_code_cli",
        prompt="Fix this issue quickly",
        force=True,
    )
    result = await _dispatch_issues(req)
    assert isinstance(result, BulkDispatchResponse)
    assert result.accepted == 1
    assert result.rejected == []


@pytest.mark.asyncio
async def test_dispatch_to_issues_invalid_number_rejected() -> None:
    """Issue number <= 0 without force → rejected with not_pickable reason."""
    req = IssueDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=-5,
        ),
        provider="claude_code_cli",
        prompt="Fix this issue quickly",
        force=False,
    )
    result = await _dispatch_issues(req)
    assert isinstance(result, BulkDispatchResponse)
    assert result.accepted == 0
    assert len(result.rejected) == 1
    assert "not_pickable" in result.rejected[0]["reason"]


@pytest.mark.asyncio
async def test_dispatch_to_issues_audit_log_has_required_fields(tmp_path: Path) -> None:
    """Audit log entries have action, provider, accepted, recorded_at, forced fields."""
    import json
    import agent_dispatch_router as adr

    original = adr._ISSUE_DISPATCH_HISTORY_PATH
    adr._ISSUE_DISPATCH_HISTORY_PATH = tmp_path / "issue_dispatch_history.json"
    try:
        req = IssueDispatchRequest(
            selection=DispatchSelection(
                mode="single",
                repository="D-sorganization/runner-dashboard",
                number=42,
            ),
            provider="claude_code_cli",
            prompt="Fix this issue",
            force=True,
        )
        result = await _dispatch_issues(req)
        assert isinstance(result, BulkDispatchResponse)

        history_path = adr._ISSUE_DISPATCH_HISTORY_PATH
        assert history_path.exists()
        history = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(history) >= 1
        entry = history[-1]
        assert entry["action"] == "agents.dispatch.issue"
        assert entry["provider"] == "claude_code_cli"
        assert "accepted" in entry
        assert "recorded_at" in entry
        assert entry["forced"] is True
    finally:
        adr._ISSUE_DISPATCH_HISTORY_PATH = original
