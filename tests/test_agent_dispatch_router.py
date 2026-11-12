"""Tests for backend/agent_dispatch_router.py (issue #82)."""

from __future__ import annotations  # noqa: E402

import asyncio  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import dispatch_quota  # noqa: E402
import quota_enforcement  # noqa: E402
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
def _reset_dispatch_quota(monkeypatch: pytest.MonkeyPatch):
    """Reset quota state and prevent unit tests from writing operator data."""
    original = dispatch_quota.quota
    dispatch_quota.quota = dispatch_quota.DispatchQuota()
    monkeypatch.setattr(
        quota_enforcement.quota_enforcement,
        "add_spend",
        lambda _principal_id, _amount_usd: None,
    )
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


@pytest.mark.asyncio
async def test_dispatch_to_issues_skips_when_premise_rejected() -> None:
    req = IssueDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=99,
        ),
        provider="claude_code_cli",
        prompt="Fix local-only-runner-guard.yml failure",
        force=False,
    )
    with patch(
        "agent_dispatch_router.evaluate_dispatch_premise",
        return_value=type(
            "P",
            (),
            {
                "allowed": False,
                "reason": "already_resolved_on_main",
                "detail": "check passing on main",
                "comment": "already resolved on main at d354634",
                "should_close": True,
            },
        )(),
    ):
        result = await _dispatch_issues(req)
        assert isinstance(result, BulkDispatchResponse)
        assert result.accepted == 0
        assert len(result.rejected) == 1
        assert "premise_rejected" in result.rejected[0]["reason"]
        assert "already_resolved_on_main" in result.rejected[0]["reason"]


@pytest.mark.asyncio
async def test_dispatch_to_issues_uses_shared_duplicate_guard_before_launch() -> None:
    req = IssueDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=99,
        ),
        provider="claude_code_cli",
        prompt="Fix this issue",
        force=False,
    )
    check = AsyncMock(
        side_effect=HTTPException(
            status_code=409,
            detail={"code": "open_pr_exists", "message": "PR #100 already references issue #99"},
        )
    )
    launch = _make_run_cmd(0)
    with (
        patch("staff.preflight_duplicate_guard.check_duplicate_pr_or_run", check),
        patch("agent_dispatch_router.evaluate_dispatch_premise") as premise,
    ):
        result = await _dispatch_issues(req, run_cmd_fn=launch)

    assert isinstance(result, BulkDispatchResponse)
    assert result.accepted == 0
    assert result.rejected[0]["reason"].startswith("open_pr_exists:")
    check.assert_awaited_once_with("D-sorganization/runner-dashboard", 99)
    premise.assert_not_awaited()
    launch.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_to_issues_model_routing_and_concurrency_queue(tmp_path: Path) -> None:
    import agent_dispatch_router as adr

    original = adr._ISSUE_DISPATCH_HISTORY_PATH
    adr._ISSUE_DISPATCH_HISTORY_PATH = tmp_path / "issue_dispatch_history.json"
    try:
        # First dispatch: dictated fix
        req1 = IssueDispatchRequest(
            selection=DispatchSelection(
                mode="single",
                repository="D-sorganization/Runner_Dashboard",
                number=1847,
            ),
            provider="claude_code_cli",
            prompt="Update Dockerfile: change `ARG NODE_MAJOR=20` to `ARG NODE_MAJOR=22`",
            force=False,
        )
        with (
            patch(
                "agent_dispatch_router.evaluate_dispatch_premise",
                return_value=type("P", (), {"allowed": True, "reason": "ok", "detail": "ok"})(),
            ),
            # Keep the launch gate hermetic: no live GitHub claim/PR lookups.
            patch("staff.preflight_duplicate_guard.check_duplicate_pr_or_run", AsyncMock(return_value=None)),
        ):
            res1 = await _dispatch_issues(req1)
            assert isinstance(res1, BulkDispatchResponse)
            assert res1.accepted == 1

            history_path = adr._ISSUE_DISPATCH_HISTORY_PATH
            assert history_path.exists()
            history = json.loads(history_path.read_text(encoding="utf-8"))
            assert len(history) >= 1
            entry = history[-1]
            assert entry["tier"] == "cli"
            assert "haiku" in entry["model"].lower()
            assert "dictated_fix" in entry["routing_reason"]

            # Second sequential dispatch on same repo is admitted after the first releases its session.
            req2 = IssueDispatchRequest(
                selection=DispatchSelection(
                    mode="single",
                    repository="D-sorganization/Runner_Dashboard",
                    number=1848,
                ),
                provider="claude_code_cli",
                prompt="Update Dockerfile: set something else",
                force=False,
            )
            res2 = await _dispatch_issues(req2)
            assert isinstance(res2, BulkDispatchResponse)
            assert res2.accepted == 1
            assert res2.rejected == []
    finally:
        adr._ISSUE_DISPATCH_HISTORY_PATH = original


@pytest.mark.asyncio
async def test_dispatch_to_issues_rejects_preflight_duplicates() -> None:
    req = IssueDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=1847,
        ),
        provider="claude_code_cli",
        prompt="Fix issue #1847",
        force=False,
    )
    conflict = HTTPException(
        status_code=409,
        detail={"code": "open_pr_exists", "message": "Open PR already targets this issue"},
    )
    with (
        patch("staff.preflight_duplicate_guard.check_duplicate_pr_or_run", side_effect=conflict),
        patch("agent_dispatch_router.evaluate_dispatch_premise", new_callable=AsyncMock) as premise,
    ):
        result = await _dispatch_issues(req)
    assert isinstance(result, BulkDispatchResponse)
    assert result.accepted == 0
    assert len(result.rejected) == 1
    assert result.rejected[0]["reason"].startswith("open_pr_exists:")
    premise.assert_not_called()


# ─── USE-1 (#1865): effort per dispatch kind and expand_epic_children ────────


def _capturing_run_cmd(captured: list[dict]) -> AsyncMock:
    async def _run(cmd: list[str], timeout: int = 30, cwd: Path | None = None) -> tuple[int, str, str]:
        captured.append(json.loads(Path(cmd[cmd.index("--input") + 1]).read_text(encoding="utf-8")))
        return 0, "", ""

    return AsyncMock(side_effect=_run)


def test_dispatch_requests_validate_effort_and_kind() -> None:
    from pydantic import ValidationError

    selection = DispatchSelection(mode="single", repository="D-sorganization/Runner_Dashboard", number=1)
    assert IssueDispatchRequest(selection=selection).dispatch_kind == "implementation"
    assert PRDispatchRequest(selection=selection).dispatch_kind == "pr"
    assert IssueDispatchRequest(selection=selection).effort is None
    with pytest.raises(ValidationError):
        IssueDispatchRequest(selection=selection, effort="extreme")
    with pytest.raises(ValidationError):
        PRDispatchRequest(selection=selection, dispatch_kind="rm -rf /")


@pytest.mark.asyncio
async def test_pr_dispatch_passes_effort_for_its_kind(tmp_path: Path) -> None:
    import agent_dispatch_router as adr

    captured: list[dict] = []
    req = PRDispatchRequest(
        selection=DispatchSelection(mode="single", repository="D-sorganization/Runner_Dashboard", number=7),
        dispatch_kind="ci_fix:lint",
    )
    with patch.object(adr, "_PR_DISPATCH_HISTORY_PATH", tmp_path / "pr.json"):
        res = await _dispatch_prs(req, run_cmd_fn=_capturing_run_cmd(captured))
    assert isinstance(res, BulkDispatchResponse) and res.accepted == 1
    inputs = captured[0]["inputs"]
    assert inputs["effort"] == "low"
    assert inputs["dispatch_kind"] == "ci_fix:lint"
    history = json.loads((tmp_path / "pr.json").read_text(encoding="utf-8"))
    assert history[-1]["effort"] == "low"


@pytest.mark.asyncio
async def test_issue_dispatch_explicit_effort_overrides_kind(tmp_path: Path) -> None:
    import agent_dispatch_router as adr

    captured: list[dict] = []
    req = IssueDispatchRequest(
        selection=DispatchSelection(mode="single", repository="D-sorganization/Runner_Dashboard", number=9),
        prompt="Implement the thing",
        effort="high",
        force=True,
    )
    with patch.object(adr, "_ISSUE_DISPATCH_HISTORY_PATH", tmp_path / "issue.json"):
        res = await _dispatch_issues(req, run_cmd_fn=_capturing_run_cmd(captured))
    assert isinstance(res, BulkDispatchResponse) and res.accepted == 1
    assert captured[0]["inputs"]["effort"] == "high"
    assert captured[0]["inputs"]["dispatch_kind"] == "implementation"


@pytest.mark.asyncio
async def test_expand_epic_children_routes_to_cli_sonnet_with_template_prompt(tmp_path: Path) -> None:
    import agent_dispatch_router as adr
    from epic_expansion import SHARED_RULE_LINKS

    captured: list[dict] = []
    req = IssueDispatchRequest(
        selection=DispatchSelection(mode="single", repository="D-sorganization/Repository_Management", number=1889),
        prompt="Split by repository.",
        dispatch_kind="expand_epic_children",
        force=True,
    )
    with patch.object(adr, "_ISSUE_DISPATCH_HISTORY_PATH", tmp_path / "issue.json"):
        res = await _dispatch_issues(req, run_cmd_fn=_capturing_run_cmd(captured))
    assert isinstance(res, BulkDispatchResponse) and res.accepted == 1
    inputs = captured[0]["inputs"]
    assert "sonnet" in inputs["model"]
    assert inputs["effort"] == "low"
    assert inputs["dispatch_kind"] == "expand_epic_children"
    assert "D-sorganization/Repository_Management#1889" in inputs["prompt"]
    assert "Split by repository." in inputs["prompt"]
    for url in SHARED_RULE_LINKS.values():
        assert url in inputs["prompt"]
    entry = json.loads((tmp_path / "issue.json").read_text(encoding="utf-8"))[-1]
    assert entry["tier"] == "cli"
    assert entry["dispatch_kind"] == "expand_epic_children"


@pytest.mark.asyncio
async def test_dispatch_issues_releases_overlap_sessions_when_gather_fails() -> None:
    """Bulk issue dispatch must release path-overlap sessions when fan-out aborts (#4272)."""
    from dispatch_routing import dispatch_queue_manager

    dispatch_queue_manager.reset_state_for_tests()
    allowed = SimpleNamespace(
        allowed=True,
        reason="",
        detail="",
        comment=None,
        should_close=False,
    )
    req = IssueDispatchRequest(
        selection=DispatchSelection(
            mode="single",
            repository="D-sorganization/runner-dashboard",
            number=77,
        ),
        provider="claude_code_cli",
        prompt="Edit `backend/dispatch_queue_link.py` for overlap cleanup.",
        force=False,
    )
    with (
        patch("agent_dispatch_router._check_issue_pickable", return_value=None),
        patch("agent_dispatch_router.evaluate_dispatch_premise", new_callable=AsyncMock, return_value=allowed),
        patch("staff.preflight_duplicate_guard.check_duplicate_pr_or_run", new_callable=AsyncMock),
        patch(
            "dispatch_queue_delay.check_dispatch_queue_delay",
            return_value=SimpleNamespace(delayed=False, reason=""),
        ),
        patch(
            "agent_dispatch_router._dispatch_one",
            new_callable=AsyncMock,
            side_effect=RuntimeError("fan-out aborted"),
        ),
    ):
        with pytest.raises(RuntimeError, match="fan-out aborted"):
            await _dispatch_issues(req)
    assert dispatch_queue_manager.get_active_sessions() == []


@pytest.mark.asyncio
async def test_dispatch_issues_bulk_failure_waits_for_sibling_before_releasing_sessions() -> None:
    """Overlap sessions must stay held until every bulk fan-out task has settled (#4272)."""
    from uuid import uuid4

    from dispatch_routing import (
        ActiveSession,
        DispatchAttemptResult,
        dispatch_queue_manager,
        extract_declared_paths,
    )

    dispatch_queue_manager.reset_state_for_tests()
    allowed = SimpleNamespace(
        allowed=True,
        reason="",
        detail="",
        comment=None,
        should_close=False,
    )
    slow_gate = asyncio.Event()

    def admit_overlap(**_kwargs: object) -> DispatchAttemptResult:
        session_id = uuid4().hex
        session = ActiveSession(
            session_id=session_id,
            repository=str(_kwargs.get("repository", "")),
            number=_kwargs.get("number"),
            declared_paths=list(_kwargs.get("declared_paths") or []),
            tier="strong",
            model="test-model",
        )
        dispatch_queue_manager._active_sessions[session_id] = session
        return DispatchAttemptResult(dispatched=True, session=session)

    async def fanout_side_effect(**kwargs: object) -> tuple[str | None, str | None, str | None]:
        number = int(kwargs.get("number", 0))
        if number == 77:
            raise RuntimeError("fast sibling failed")
        await slow_gate.wait()
        return ("env-slow", "fp-slow", None)

    req = IssueDispatchRequest(
        selection=DispatchSelection(
            mode="list",
            items=[
                DispatchItem(repository="D-sorganization/runner-dashboard", number=77),
                DispatchItem(repository="D-sorganization/runner-dashboard", number=78),
            ],
        ),
        provider="claude_code_cli",
        prompt="Edit `backend/slow_a.py` and `backend/slow_b.py`.",
        force=False,
    )
    with (
        patch("agent_dispatch_router._check_issue_pickable", return_value=None),
        patch("agent_dispatch_router.evaluate_dispatch_premise", new_callable=AsyncMock, return_value=allowed),
        patch("staff.preflight_duplicate_guard.check_duplicate_pr_or_run", new_callable=AsyncMock),
        patch(
            "dispatch_queue_delay.check_dispatch_queue_delay",
            return_value=SimpleNamespace(delayed=False, reason=""),
        ),
        patch.object(dispatch_queue_manager, "try_dispatch", side_effect=admit_overlap),
        patch(
            "agent_dispatch_router._dispatch_one",
            new_callable=AsyncMock,
            side_effect=fanout_side_effect,
        ),
    ):
        dispatch_task = asyncio.create_task(_dispatch_issues(req))
        await asyncio.sleep(0.05)
        assert not slow_gate.is_set()
        assert len(dispatch_queue_manager.get_active_sessions()) == 2
        overlap_paths = extract_declared_paths("Edit `backend/slow_a.py`.")
        allowed_overlap, blocker = dispatch_queue_manager.can_dispatch(
            "D-sorganization/runner-dashboard",
            overlap_paths,
        )
        assert not allowed_overlap
        assert blocker
        slow_gate.set()
        with pytest.raises(RuntimeError, match="fast sibling failed"):
            await dispatch_task

    assert dispatch_queue_manager.get_active_sessions() == []


@pytest.mark.asyncio
async def test_dispatch_issues_bulk_cancel_mid_gather_releases_overlap_sessions() -> None:
    """Caller cancellation during bulk fan-out must still release overlap sessions (#4272)."""
    from uuid import uuid4

    from dispatch_routing import (
        ActiveSession,
        DispatchAttemptResult,
        dispatch_queue_manager,
    )

    dispatch_queue_manager.reset_state_for_tests()
    allowed = SimpleNamespace(
        allowed=True,
        reason="",
        detail="",
        comment=None,
        should_close=False,
    )
    slow_gate = asyncio.Event()

    def admit_overlap(**_kwargs: object) -> DispatchAttemptResult:
        session_id = uuid4().hex
        session = ActiveSession(
            session_id=session_id,
            repository=str(_kwargs.get("repository", "")),
            number=_kwargs.get("number"),
            declared_paths=list(_kwargs.get("declared_paths") or []),
            tier="strong",
            model="test-model",
        )
        dispatch_queue_manager._active_sessions[session_id] = session
        return DispatchAttemptResult(dispatched=True, session=session)

    async def fanout_side_effect(**_kwargs: object) -> tuple[str | None, str | None, str | None]:
        await slow_gate.wait()
        return ("env", "fp", None)

    req = IssueDispatchRequest(
        selection=DispatchSelection(
            mode="list",
            items=[
                DispatchItem(repository="D-sorganization/runner-dashboard", number=77),
                DispatchItem(repository="D-sorganization/runner-dashboard", number=78),
            ],
        ),
        provider="claude_code_cli",
        prompt="Edit `backend/cancel_a.py` and `backend/cancel_b.py`.",
        force=False,
    )
    with (
        patch("agent_dispatch_router._check_issue_pickable", return_value=None),
        patch("agent_dispatch_router.evaluate_dispatch_premise", new_callable=AsyncMock, return_value=allowed),
        patch("staff.preflight_duplicate_guard.check_duplicate_pr_or_run", new_callable=AsyncMock),
        patch(
            "dispatch_queue_delay.check_dispatch_queue_delay",
            return_value=SimpleNamespace(delayed=False, reason=""),
        ),
        patch.object(dispatch_queue_manager, "try_dispatch", side_effect=admit_overlap),
        patch(
            "agent_dispatch_router._dispatch_one",
            new_callable=AsyncMock,
            side_effect=fanout_side_effect,
        ),
    ):
        dispatch_task = asyncio.create_task(_dispatch_issues(req))
        await asyncio.sleep(0.05)
        assert len(dispatch_queue_manager.get_active_sessions()) == 2
        dispatch_task.cancel()
        slow_gate.set()
        with pytest.raises(asyncio.CancelledError):
            await dispatch_task

    assert dispatch_queue_manager.get_active_sessions() == []


@pytest.mark.asyncio
async def test_dispatch_issues_double_cancel_during_post_cancel_gather_releases_once() -> None:
    """A second cancel while waiting for siblings must still release overlap sessions exactly once (#4375)."""
    from uuid import uuid4

    from dispatch_routing import (
        ActiveSession,
        DispatchAttemptResult,
        dispatch_queue_manager,
    )

    dispatch_queue_manager.reset_state_for_tests()
    allowed = SimpleNamespace(
        allowed=True,
        reason="",
        detail="",
        comment=None,
        should_close=False,
    )
    slow_gate = asyncio.Event()

    def admit_overlap(**_kwargs: object) -> DispatchAttemptResult:
        session_id = uuid4().hex
        session = ActiveSession(
            session_id=session_id,
            repository=str(_kwargs.get("repository", "")),
            number=_kwargs.get("number"),
            declared_paths=list(_kwargs.get("declared_paths") or []),
            tier="strong",
            model="test-model",
        )
        dispatch_queue_manager._active_sessions[session_id] = session
        return DispatchAttemptResult(dispatched=True, session=session)

    async def fanout_side_effect(**_kwargs: object) -> tuple[str | None, str | None, str | None]:
        await slow_gate.wait()
        return ("env", "fp", None)

    release_calls: list[str] = []
    real_release = dispatch_queue_manager.release_session

    def track_release(session_id: str, **kwargs: object) -> object:
        release_calls.append(session_id)
        return real_release(session_id, **kwargs)

    shield_invocations = 0
    real_shield = asyncio.shield

    async def first_gather_shield_not_deferred(awaitable: object) -> object:
        """Let cancellation land mid-gather so the post-cancel sibling wait is real."""
        nonlocal shield_invocations
        shield_invocations += 1
        if shield_invocations == 1:
            return await awaitable  # type: ignore[misc]
        return await real_shield(awaitable)  # type: ignore[arg-type]

    req = IssueDispatchRequest(
        selection=DispatchSelection(
            mode="list",
            items=[
                DispatchItem(repository="D-sorganization/runner-dashboard", number=77),
                DispatchItem(repository="D-sorganization/runner-dashboard", number=78),
            ],
        ),
        provider="claude_code_cli",
        prompt="Edit `backend/cancel_a.py` and `backend/cancel_b.py`.",
        force=False,
    )
    with (
        patch("agent_dispatch_router._check_issue_pickable", return_value=None),
        patch("agent_dispatch_router.evaluate_dispatch_premise", new_callable=AsyncMock, return_value=allowed),
        patch("staff.preflight_duplicate_guard.check_duplicate_pr_or_run", new_callable=AsyncMock),
        patch(
            "dispatch_queue_delay.check_dispatch_queue_delay",
            return_value=SimpleNamespace(delayed=False, reason=""),
        ),
        patch.object(dispatch_queue_manager, "try_dispatch", side_effect=admit_overlap),
        patch(
            "agent_dispatch_router._dispatch_one",
            new_callable=AsyncMock,
            side_effect=fanout_side_effect,
        ),
        patch.object(dispatch_queue_manager, "release_session", side_effect=track_release),
        patch("agent_dispatch_router.asyncio.shield", side_effect=first_gather_shield_not_deferred),
    ):
        dispatch_task = asyncio.create_task(_dispatch_issues(req))
        await asyncio.sleep(0.05)
        held = dispatch_queue_manager.get_active_sessions()
        assert len(held) == 2
        session_ids = {s.session_id for s in held}

        dispatch_task.cancel()
        for _ in range(50):
            if dispatch_task.cancelling():
                break
            await asyncio.sleep(0)
        dispatch_task.cancel()

        async def unblock_sibling_gather() -> None:
            await asyncio.sleep(0.02)
            slow_gate.set()

        unblock_task = asyncio.create_task(unblock_sibling_gather())
        try:
            with pytest.raises(asyncio.CancelledError):
                await dispatch_task
        finally:
            await unblock_task

    assert dispatch_queue_manager.get_active_sessions() == []
    assert set(release_calls) == session_ids
    assert len(release_calls) == len(session_ids)
