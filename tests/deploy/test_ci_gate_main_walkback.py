"""CI gate must deploy newest main commit with complete green checks, not only HEAD."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.ci_gate import (  # noqa: E402
    ci_gate_for_commit,
    parse_check_runs_payload,
    select_deployable_main_commit,
)

_HEAD_FIXTURE = REPO / "tests" / "deploy" / "fixtures" / "916a571_check_runs.json"
_GREEN_FIXTURE = REPO / "tests" / "deploy" / "fixtures" / "4ece23d_check_runs.json"
_HEAD_SHA = "916a571bae7b8eb45134ac02af4009a9877dec92"  # pragma: allowlist secret
_GREEN_SHA = "4ece23d6a70f6c1eb3acc276f38c490ef6793a07"  # pragma: allowlist secret


def _runs_from_fixture(path: Path, sha: str) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload["check_runs"] if isinstance(payload, dict) else payload
    return [{**run, "head_sha": sha} for run in raw]


def test_recorded_head_has_build_and_scan_still_running() -> None:
    runs = _runs_from_fixture(_HEAD_FIXTURE, _HEAD_SHA)
    docker = [r for r in runs if r.get("name") == "Build and scan image"]
    assert docker, "fixture must include Build and scan image"
    assert any(r.get("status") in ("queued", "in_progress") for r in docker)
    ok, detail = ci_gate_for_commit(runs, _HEAD_SHA)
    assert ok is False
    assert "Build and scan image" in detail


def test_recorded_parent_commit_is_fully_green() -> None:
    runs = _runs_from_fixture(_GREEN_FIXTURE, _GREEN_SHA)
    ok, detail = ci_gate_for_commit(runs, _GREEN_SHA)
    assert ok is True
    assert _GREEN_SHA[:7] in detail


def test_select_deployable_skips_head_pending_and_picks_green_parent() -> None:
    runs_by_sha = {
        _HEAD_SHA: _runs_from_fixture(_HEAD_FIXTURE, _HEAD_SHA),
        _GREEN_SHA: _runs_from_fixture(_GREEN_FIXTURE, _GREEN_SHA),
    }

    def fetch(sha: str) -> list[dict[str, object]]:
        return runs_by_sha[sha]

    chosen, detail = select_deployable_main_commit([_HEAD_SHA, _GREEN_SHA], fetch)
    assert chosen == _GREEN_SHA
    assert _GREEN_SHA[:7] in detail


def test_select_deployable_fails_closed_when_window_has_no_green() -> None:
    runs = _runs_from_fixture(_HEAD_FIXTURE, _HEAD_SHA)

    def fetch(_sha: str) -> list[dict[str, object]]:
        return runs

    chosen, detail = select_deployable_main_commit([_HEAD_SHA], fetch)
    assert chosen is None
    assert "no green commit" in detail


def test_cancelled_check_on_skipped_commit_does_not_block_newer_green() -> None:
    """HEAD pending; middle commit cancelled docker; older commit green."""
    head = _runs_from_fixture(_HEAD_FIXTURE, _HEAD_SHA)
    green = _runs_from_fixture(_GREEN_FIXTURE, _GREEN_SHA)
    cancelled_sha = "a" * 40
    cancelled = [
        {
            "head_sha": cancelled_sha,
            "name": "Build and scan image",
            "status": "completed",
            "conclusion": "cancelled",
        },
        {
            "head_sha": cancelled_sha,
            "name": "quality-gate",
            "status": "completed",
            "conclusion": "success",
        },
    ]
    runs_by_sha = {_HEAD_SHA: head, cancelled_sha: cancelled, _GREEN_SHA: green}

    def fetch(sha: str) -> list[dict[str, object]]:
        return runs_by_sha[sha]

    chosen, _ = select_deployable_main_commit([_HEAD_SHA, cancelled_sha, _GREEN_SHA], fetch)
    assert chosen == _GREEN_SHA


def test_evaluate_cycle_uses_walkback_commit(tmp_path: Path) -> None:
    from node_auto_deploy.runner import CycleConfig, CycleDeps, evaluate_cycle  # noqa: E402
    from node_auto_deploy.state import AutoDeployState  # noqa: E402

    deploy = tmp_path / "dashboard"
    deploy.mkdir()
    (deploy / "deployment.json").write_text('{"git_sha": "' + ("e" * 40) + '"}', encoding="utf-8")

    def gh(path: str):
        if path.endswith("/commits/main"):
            return {"sha": _HEAD_SHA}
        if "commits?sha=main" in path:
            return [{"sha": _HEAD_SHA}, {"sha": _GREEN_SHA}]
        if _HEAD_SHA in path and "check-runs" in path:
            return {"check_runs": json.loads(_HEAD_FIXTURE.read_text())["check_runs"]}
        if _GREEN_SHA in path and "check-runs" in path:
            return {"check_runs": json.loads(_GREEN_FIXTURE.read_text())["check_runs"]}
        return {}

    releases = [
        {
            "draft": False,
            "target_commitish": _GREEN_SHA,
            "assets": [
                {"name": "dashboard-1.0.0.tar.gz", "url": "https://example/tar"},
                {"name": "dashboard-1.0.0.tar.gz.sha256", "url": "https://example/sum"},
            ],
        }
    ]

    cfg = CycleConfig(
        repo_full_name="D-sorganization/Runner_Dashboard",
        live_deploy_dir=str(deploy),
        machine_role="node",
        hub_url=None,
        fleet_token=None,
    )
    deps = CycleDeps(
        github_get=gh,
        list_releases=lambda: releases,
        fetch_fleet_status=lambda: {},
        staff_in_flight=lambda: 0,
        state=AutoDeployState(),
    )
    result, msg, target = evaluate_cycle(cfg, deps)
    assert result == "ready"
    assert target == _GREEN_SHA
    assert "1.0.0" in msg
