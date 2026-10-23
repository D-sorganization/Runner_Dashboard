"""Bot-maintenance check-runs must not block node auto-deploy."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.ci_gate import (  # noqa: E402
    NON_BLOCKING_DEPLOY_CHECK_NAMES,
    ci_gate_for_deploy_commit,
)

_SHA = "a" * 40


def _run(name: str, status: str, conclusion: str | None, idx: int) -> dict[str, object]:
    return {
        "id": idx,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": _SHA,
        "app": {"id": 1},
        "check_suite": {"id": idx},
        "started_at": "2026-01-01T00:00:00Z",
    }


def test_queued_verify_closure_does_not_block_deploy() -> None:
    runs = [_run("quality-gate", "completed", "success", 1), _run("verify-closure", "queued", None, 2)]
    ok, detail = ci_gate_for_deploy_commit(runs, _SHA)
    assert ok is True, detail


def test_cancelled_bot_jobs_do_not_block_deploy() -> None:
    runs = [_run("quality-gate", "completed", "success", 1)]
    runs += [_run(n, "completed", "cancelled", i + 2) for i, n in enumerate(["verify-closure", "reap", "sweep"])]
    ok, detail = ci_gate_for_deploy_commit(runs, _SHA)
    assert ok is True, detail


def test_real_quality_check_still_blocks() -> None:
    runs = [_run("quality-gate", "completed", "failure", 1), _run("verify-closure", "completed", "success", 2)]
    ok, detail = ci_gate_for_deploy_commit(runs, _SHA)
    assert ok is False
    assert "quality-gate" in detail


def test_verify_closure_listed() -> None:
    assert "verify-closure" in NON_BLOCKING_DEPLOY_CHECK_NAMES
