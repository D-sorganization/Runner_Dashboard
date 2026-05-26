"""Tests for backend/quick_dispatch.py (issue #85)."""

from __future__ import annotations  # noqa: E402

import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from quick_dispatch import (  # noqa: E402
    QuickDispatchHealthGateResult,
    QuickDispatchRequest,
    _quick_dispatch_health_cache,
    _quick_dispatch_timestamps,
    quick_dispatch,
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


def _available_provider_map() -> dict[str, object]:
    return {
        "claude_code_cli": type(
            "A",
            (),
            {"available": True, "status": "available", "detail": "ready"},
        )(),
    }


async def _call(req: QuickDispatchRequest, run_cmd_fn=None, extra_patches: dict | None = None):
    if run_cmd_fn is None:
        run_cmd_fn = _make_run_cmd(0)
    _quick_dispatch_health_cache.clear()
    with patch("quick_dispatch.agent_remediation.probe_provider_availability") as mock_avail:
        mock_avail.return_value = _available_provider_map()
        return await quick_dispatch(
            req,
            run_cmd_fn=run_cmd_fn,
            org="D-sorganization",
            repo_root=Path("."),
            normalize_repository_fn=_normalize,
            health_gate_fn=AsyncMock(return_value=QuickDispatchHealthGateResult(ready=True)),
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
    with patch("quick_dispatch.agent_remediation.probe_provider_availability") as mock_avail:
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


@pytest.mark.asyncio
async def test_not_ready_returns_structured_rejection() -> None:
    """A failed readiness/runner gate returns not_ready before dispatch."""
    _quick_dispatch_timestamps.clear()
    _quick_dispatch_health_cache.clear()
    req = QuickDispatchRequest(
        repository="D-sorganization/runner-dashboard",
        prompt="Fix the failing test in test_api.py",
        provider="claude_code_cli",
    )
    with patch("quick_dispatch.agent_remediation.probe_provider_availability") as mock_avail:
        mock_avail.return_value = _available_provider_map()
        resp = await quick_dispatch(
            req,
            run_cmd_fn=_make_run_cmd(0),
            org="D-sorganization",
            repo_root=Path("."),
            normalize_repository_fn=_normalize,
            health_gate_fn=AsyncMock(
                return_value=QuickDispatchHealthGateResult(
                    ready=False,
                    reason="no_online_runners",
                    retry_after_seconds=30,
                )
            ),
        )
    assert resp.accepted is False
    assert resp.error_code == "not_ready"
    assert resp.reason == "no_online_runners"
    assert resp.retry_after_seconds == 30


@pytest.mark.asyncio
async def test_force_bypasses_health_gate_and_logs_override() -> None:
    """force=True skips the health gate and emits an audit warning."""
    _quick_dispatch_timestamps.clear()
    _quick_dispatch_health_cache.clear()
    req = QuickDispatchRequest(
        repository="D-sorganization/runner-dashboard",
        prompt="Fix the failing test in test_api.py",
        provider="claude_code_cli",
        force=True,
        requested_by="operator",
        principal="operator",
    )
    health_gate = AsyncMock(
        return_value=QuickDispatchHealthGateResult(
            ready=False,
            reason="no_online_runners",
            retry_after_seconds=30,
        )
    )
    with (
        patch("quick_dispatch.agent_remediation.probe_provider_availability") as mock_avail,
        patch("quick_dispatch.log.warning") as mock_warning,
    ):
        mock_avail.return_value = _available_provider_map()
        resp = await quick_dispatch(
            req,
            run_cmd_fn=_make_run_cmd(0),
            org="D-sorganization",
            repo_root=Path("."),
            normalize_repository_fn=_normalize,
            health_gate_fn=health_gate,
        )
    assert resp.accepted is True
    health_gate.assert_not_called()
    assert any(call.args and call.args[0].startswith("dispatch.force_override") for call in mock_warning.call_args_list)


@pytest.mark.asyncio
async def test_health_gate_cached_for_five_seconds() -> None:
    """Repeated dispatch attempts reuse the cached health result for 5 seconds."""
    _quick_dispatch_timestamps.clear()
    _quick_dispatch_health_cache.clear()
    req = QuickDispatchRequest(
        repository="D-sorganization/runner-dashboard",
        prompt="Fix the failing test in test_api.py",
        provider="claude_code_cli",
    )
    health_gate = AsyncMock(return_value=QuickDispatchHealthGateResult(ready=True))
    monotonic_values = iter(
        [
            100.0,
            100.0,
            101.0,
            101.0,
            102.0,
            102.0,
            103.0,
            103.0,
            104.0,
            104.0,
            105.0,
            105.0,
            106.0,
            106.0,
            107.0,
            107.0,
            108.0,
            108.0,
            109.0,
            109.0,
        ]
    )
    with (
        patch("quick_dispatch.agent_remediation.probe_provider_availability") as mock_avail,
        patch("quick_dispatch.time.monotonic", side_effect=lambda: next(monotonic_values)),
    ):
        mock_avail.return_value = _available_provider_map()
        for _ in range(5):
            resp = await quick_dispatch(
                req,
                run_cmd_fn=_make_run_cmd(0),
                org="D-sorganization",
                repo_root=Path("."),
                normalize_repository_fn=_normalize,
                health_gate_fn=health_gate,
            )
            assert resp.accepted is True
    assert health_gate.await_count == 1
