"""Tests for backend/quick_dispatch.py (issue #85)."""

from __future__ import annotations  # noqa: E402

import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from quick_dispatch import (  # noqa: E402
    QuickDispatchRequest,
    _quick_dispatch_timestamps,
    quick_dispatch,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_run_cmd(returncode: int = 0, stdout: str = "", stderr: str = "") -> AsyncMock:
    async def _run(
        cmd: list[str], timeout: int = 30, cwd: Path | None = None
    ) -> tuple[int, str, str]:
        return returncode, stdout, stderr

    return AsyncMock(side_effect=_run)


def _normalize(value: str) -> tuple[str, str]:
    if "/" in value:
        parts = value.split("/", 1)
        return parts[1], value
    return value, f"D-sorganization/{value}"


async def _call(
    req: QuickDispatchRequest, run_cmd_fn=None, extra_patches: dict | None = None
):
    if run_cmd_fn is None:
        run_cmd_fn = _make_run_cmd(0)
    with patch(
        "quick_dispatch.agent_remediation.probe_provider_availability"
    ) as mock_avail:
        mock_avail.return_value = {
            "claude_code_cli": type(
                "A",
                (),
                {"available": True, "status": "available", "detail": "ready"},
            )(),
        }
        return await quick_dispatch(
            req,
            run_cmd_fn=run_cmd_fn,
            org="D-sorganization",
            repo_root=Path("."),
            normalize_repository_fn=_normalize,
        )


# ─── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_accepted() -> None:
    """Happy path: well-formed request with a mocked gh call → accepted=True."""
    _quick_dispatch_timestamps.clear()
    req = QuickDispatchRequest(
        repository="D-sorganization/runner-dashboard",
        prompt="Fix the failing test in test_api.py",
        provider="claude_code_cli",
    )
    resp = await _call(req)
    assert resp.accepted is True
    assert resp.envelope_id
    assert resp.fingerprint
    assert resp.history_id


@pytest.mark.asyncio
async def test_provider_unavailable_rejected() -> None:
    """Unknown provider → accepted=False with provider_unavailable reason."""
    _quick_dispatch_timestamps.clear()
    req = QuickDispatchRequest(
        repository="D-sorganization/runner-dashboard",
        prompt="Fix the failing test in test_api.py",
        provider="nonexistent_provider",
    )
    with patch(
        "quick_dispatch.agent_remediation.probe_provider_availability"
    ) as mock_avail:
        mock_avail.return_value = {}
        resp = await quick_dispatch(
            req,
            run_cmd_fn=_make_run_cmd(0),
            org="D-sorganization",
            repo_root=Path("."),
            normalize_repository_fn=_normalize,
        )
    assert resp.accepted is False
    assert "provider_unavailable" in resp.reason


@pytest.mark.asyncio
async def test_prompt_too_short_rejected() -> None:
    """Prompt shorter than 10 chars → accepted=False."""
    _quick_dispatch_timestamps.clear()
    req = QuickDispatchRequest(
        repository="D-sorganization/runner-dashboard",
        prompt="short",
        provider="claude_code_cli",
    )
    resp = await _call(req)
    assert resp.accepted is False
    assert "prompt_too_short" in resp.reason


@pytest.mark.asyncio
async def test_rate_limit_after_10_calls() -> None:
    """11th call within 60s window returns rate_limited reason."""
    _quick_dispatch_timestamps.clear()
    req = QuickDispatchRequest(
        repository="D-sorganization/runner-dashboard",
        prompt="Fix the failing test in test_api.py",
        provider="claude_code_cli",
    )
    # Make 10 accepted calls
    for _ in range(10):
        resp = await _call(req)
        assert resp.accepted is True

    # 11th call should be rate-limited
    resp = await _call(req)
    assert resp.accepted is False
    assert "rate_limited" in resp.reason

    _quick_dispatch_timestamps.clear()


@pytest.mark.asyncio
async def test_workflow_missing_returns_not_configured() -> None:
    """When gh returns an error indicating workflow not found → workflow_not_configured."""
    _quick_dispatch_timestamps.clear()
    run_cmd_fn = _make_run_cmd(
        returncode=1,
        stderr="HTTP 422: Workflow does not exist",
    )
    req = QuickDispatchRequest(
        repository="D-sorganization/runner-dashboard",
        prompt="Fix the failing test in test_api.py",
        provider="claude_code_cli",
    )
    resp = await _call(req, run_cmd_fn=run_cmd_fn)
    assert resp.accepted is False
    assert "workflow_not_configured" in resp.reason
