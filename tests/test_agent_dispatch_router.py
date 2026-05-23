"""Tests for backend/agent_dispatch_router.py (issue #82)."""

from __future__ import annotations  # noqa: E402

import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import dispatch_quota  # noqa: E402
from agent_dispatch_router import (  # noqa: E402
    BulkDispatchResponse,
    DispatchItem,
    DispatchSelection,
    IssueDispatchRequest,
    PRDispatchRequest,
    _resolve_targets,
    _validate_provider,
    dispatch_to_issues,
    dispatch_to_prs,
)


@pytest.fixture(autouse=True)
def _reset_dispatch_quota():
    """Reset the in-memory hourly quota between tests so cap accounting starts fresh."""
    original = dispatch_quota.quota
    dispatch_quota.quota = dispatch_quota.DispatchQuota()
    try:
        yield
    finally:
        dispatch_quota.quota = original


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
    # Issue #408: dispatch_to_prs requires a non-anonymous principal.  Tests
    # that don't supply one are augmented here with a deterministic id.
    if not req.principal:
        req.principal = "test-principal"
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
    if not req.principal:
        req.principal = "test-principal"
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
        principal="test-principal-unavailable",
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


# ─── Direct helper coverage for provider / target resolution ─────────────────


def test_validate_provider_unknown_provider_returns_reason() -> None:
    reason = _validate_provider("unknown_provider")
    assert reason == "provider_unavailable: unknown provider 'unknown_provider'"


def test_resolve_targets_repo_mode_requires_repository() -> None:
    selection = DispatchSelection(mode="repo")
    assert _resolve_targets(selection, _normalize, "D-sorganization") == "repo mode requires repository"


def test_resolve_targets_repo_mode_returns_sentinel_target() -> None:
    selection = DispatchSelection(mode="repo", repository="runner-dashboard")
    assert _resolve_targets(selection, _normalize, "D-sorganization") == [("D-sorganization/runner-dashboard", -1)]


def test_resolve_targets_list_mode_requires_items() -> None:
    selection = DispatchSelection(mode="list")
    assert _resolve_targets(selection, _normalize, "D-sorganization") == "list mode requires at least one item"


def test_resolve_targets_unknown_mode_returns_error() -> None:
    selection = SimpleNamespace(mode="mystery", repository="", number=None, items=[])
    assert _resolve_targets(selection, _normalize, "D-sorganization") == "unknown selection mode: mystery"


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
async def test_dispatch_to_issues_anonymous_principal_rejected() -> None:
    req = IssueDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=42,
        ),
        provider="claude_code_cli",
        prompt="Fix this issue",
        principal="",
    )
    result = await dispatch_to_issues(
        req,
        run_cmd_fn=_make_run_cmd(0),
        org="D-sorganization",
        repo_root=Path("."),
        normalize_repository_fn=_normalize,
    )
    assert isinstance(result, dict)
    assert result["status_code"] == 422
    assert "anonymous_principal" in result["error"]


@pytest.mark.asyncio
async def test_dispatch_to_issues_rate_limited_returns_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _deny(_principal: str) -> dict[str, int | bool | str]:
        return {"allowed": False, "reason": "rate_limited", "retry_after": 17}

    monkeypatch.setattr(dispatch_quota.quota, "check_and_record", _deny)
    req = IssueDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=42,
        ),
        provider="claude_code_cli",
        prompt="Fix this issue",
        principal="rate-limited-user",
    )
    result = await _dispatch_issues(req)
    assert isinstance(result, dict)
    assert result["status_code"] == 429
    assert result["retry_after"] == 17


@pytest.mark.asyncio
async def test_dispatch_to_issues_gh_failure_populates_rejected() -> None:
    req = IssueDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=11,
        ),
        provider="claude_code_cli",
        prompt="Fix this issue quickly",
        force=True,
    )
    result = await _dispatch_issues(req, run_cmd_fn=_make_run_cmd(returncode=1, stderr="some gh error"))
    assert isinstance(result, BulkDispatchResponse)
    assert result.accepted == 0
    assert result.rejected == [
        {
            "repository": "D-sorganization/runner-dashboard",
            "number": 11,
            "reason": "dispatch_failed: gh exited with code 1",
        }
    ]


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
