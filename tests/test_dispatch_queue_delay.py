"""Contract tests for dispatch queue-depth throttling (RD-CI-033, issue #2034)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agent_dispatch_router import (  # noqa: E402
    DispatchSelection,
    IssueDispatchRequest,
    PRDispatchRequest,
    dispatch_to_issues,
    dispatch_to_prs,
)
from dispatch_queue_delay import (  # noqa: E402
    DispatchLaneContext,
    QueueDelayPolicy,
    QueueDelayVerdict,
    QueueDepthSnapshot,
    check_dispatch_queue_delay,
    evaluate_queue_depth_delay,
    load_queue_delay_policy,
)


def _avail_patch():
    return patch(
        "agent_remediation.probe_provider_availability",
        return_value={"antigravity": type("A", (), {"available": True, "status": "ok", "detail": ""})()},
    )


def _normalize(name: str) -> tuple[str, str]:
    short = name.split("/")[-1]
    return short, f"D-sorganization/{short}"


async def _async_cmd(*args, **kwargs) -> tuple[int, str, str]:
    return 0, '{"id": 1, "status": "completed"}', ""


def test_default_policy_and_disabled_state() -> None:
    pol = QueueDelayPolicy.default()
    assert pol.enabled is False and pol.shadow_mode is True
    snap = QueueDepthSnapshot(queued_runs=50, queued_jobs=200, queue_p95_seconds=1200.0)
    ctx = DispatchLaneContext(tier="agent", lane="agent", dispatch_kind="feature")
    v = evaluate_queue_depth_delay(ctx, metrics=snap, policy=pol)
    assert v.allowed is True and v.delayed is False and "enabled=false" in v.reason


@pytest.mark.parametrize(
    ("tier", "lane", "kind"),
    [("user", "user", ""), ("repair", "repair", "ci_fix"), ("agent", "repair", "conflict_resolution")],
)
def test_exempt_lanes_bypass_even_when_enforcing(tier: str, lane: str, kind: str) -> None:
    pol = QueueDelayPolicy(
        enabled=True, shadow_mode=False, max_queued_runs=2, max_queued_jobs=5, max_queue_p95_seconds=100.0
    )
    snap = QueueDepthSnapshot(queued_runs=25, queued_jobs=80, queue_p95_seconds=900.0)
    v = evaluate_queue_depth_delay(
        DispatchLaneContext(tier=tier, lane=lane, dispatch_kind=kind), metrics=snap, policy=pol
    )
    assert v.allowed is True and v.delayed is False and "exempt" in v.reason.lower()


def test_shadow_mode_and_threshold_enforcement() -> None:
    pol_shadow = QueueDelayPolicy(
        enabled=True, shadow_mode=True, max_queued_runs=5, max_queued_jobs=10, max_queue_p95_seconds=300.0
    )
    snap_high = QueueDepthSnapshot(queued_runs=10, queued_jobs=25, queue_p95_seconds=400.0)
    ctx = DispatchLaneContext(tier="agent", lane="agent", dispatch_kind="feature")
    v_shadow = evaluate_queue_depth_delay(ctx, metrics=snap_high, policy=pol_shadow)
    assert v_shadow.allowed is True and v_shadow.delayed is False and v_shadow.shadow_would_delay is True

    pol_enforce = QueueDelayPolicy(
        enabled=True, shadow_mode=False, max_queued_runs=5, max_queued_jobs=10, max_queue_p95_seconds=300.0
    )
    assert (
        evaluate_queue_depth_delay(
            ctx, metrics=QueueDepthSnapshot(queued_runs=2, queued_jobs=4), policy=pol_enforce
        ).allowed
        is True
    )
    assert (
        evaluate_queue_depth_delay(ctx, metrics=QueueDepthSnapshot(queued_runs=6), policy=pol_enforce).delayed is True
    )
    assert (
        evaluate_queue_depth_delay(ctx, metrics=QueueDepthSnapshot(queued_jobs=15), policy=pol_enforce).delayed is True
    )
    assert (
        evaluate_queue_depth_delay(ctx, metrics=QueueDepthSnapshot(queue_p95_seconds=350.0), policy=pol_enforce).delayed
        is True
    )


def test_config_load_and_helper(tmp_path: Path) -> None:
    cfg = tmp_path / "dispatch_limits.json"
    cfg.write_text(
        '{"version": 1, "queue_depth_delay": {"enabled": true, "shadow_mode": false, "max_queued_runs": 8}}',
        encoding="utf-8",
    )
    pol = load_queue_delay_policy(config_path=cfg)
    assert pol.enabled is True and pol.shadow_mode is False and pol.max_queued_runs == 8
    snap = QueueDepthSnapshot(queued_runs=20)
    assert (
        check_dispatch_queue_delay(repo_root=tmp_path, principal="agt", dispatch_kind="feat", metrics=snap).delayed
        is True
    )
    assert check_dispatch_queue_delay(repo_root=tmp_path, principal="agt", is_repair=True, metrics=snap).allowed is True


@pytest.mark.asyncio
async def test_router_integration_prs_and_issues(tmp_path: Path) -> None:
    cfg = tmp_path / "config" / "dispatch_limits.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('{"version": 1, "queue_depth_delay": {"enabled": true, "shadow_mode": false}}', encoding="utf-8")
    req_pr = PRDispatchRequest(
        selection=DispatchSelection(mode="single", repository="D-sorganization/Runner_Dashboard", number=76),
        prompt="p",
        provider="antigravity",
        principal="agt",
        dispatch_kind="feature",
    )
    v_delay = QueueDelayVerdict(
        allowed=False, delayed=True, shadow_would_delay=False, reason="delayed_queue_depth: capped"
    )

    with _avail_patch(), patch("dispatch_queue_delay.check_dispatch_queue_delay", return_value=v_delay):
        res = await dispatch_to_prs(
            req_pr, run_cmd_fn=_async_cmd, org="D-sorganization", repo_root=tmp_path, normalize_repository_fn=_normalize
        )
        assert isinstance(res, dict) and res.get("status_code") == 429 and "delayed_queue_depth" in res.get("error", "")

    req_issue = IssueDispatchRequest(
        selection=DispatchSelection(mode="single", repository="D-sorganization/Runner_Dashboard", number=10),
        prompt="p",
        provider="antigravity",
        principal="agt",
        dispatch_kind="feature",
    )
    with _avail_patch(), patch("dispatch_queue_delay.check_dispatch_queue_delay", return_value=v_delay):
        res_i = await dispatch_to_issues(
            req_issue,
            run_cmd_fn=_async_cmd,
            org="D-sorganization",
            repo_root=tmp_path,
            normalize_repository_fn=_normalize,
        )
        assert len(res_i.rejected) == 1 and "delayed_queue_depth" in res_i.rejected[0]["reason"]

    req_repair = IssueDispatchRequest(
        selection=DispatchSelection(mode="single", repository="D-sorganization/Runner_Dashboard", number=10),
        prompt="p",
        provider="antigravity",
        principal="agt",
        dispatch_kind="ci_fix",
    )
    with _avail_patch():
        res_rep = await dispatch_to_issues(
            req_repair,
            run_cmd_fn=_async_cmd,
            org="D-sorganization",
            repo_root=tmp_path,
            normalize_repository_fn=_normalize,
        )
        assert not any("delayed_queue_depth" in r.get("reason", "") for r in res_rep.rejected)
