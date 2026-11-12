"""Auto-deploy must raise attention when green main goes unreleased (RD #2932)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.ci_gate import scan_main_window  # noqa: E402
from node_auto_deploy.contracts import AutoDeployState  # noqa: E402
from node_auto_deploy.runner import CycleConfig, CycleDeps, evaluate_cycle  # noqa: E402
from node_auto_deploy.staleness import assess_release_staleness  # noqa: E402
from node_auto_deploy.state import record_event  # noqa: E402

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)  # noqa: UP017
SHAS = [f"{i:x}" * 40 for i in range(1, 8)]  # newest first
RELEASED = SHAS[-1]


def _run(sha: str, conclusion: str) -> dict[str, Any]:
    return {
        "id": int(sha[0], 16),
        "head_sha": sha,
        "name": "quality-gate",
        "status": "completed",
        "conclusion": conclusion,
        "app": {"id": 1},
        "check_suite": {"id": int(sha[0], 16)},
        "started_at": "2026-01-01T00:00:00Z",
    }


def _fetch(red: frozenset[str] = frozenset()):
    return lambda sha: [_run(sha, "failure" if sha in red else "success")]


def _scan(red: frozenset[str] = frozenset(), released: set[str] | None = None):
    rel = {RELEASED} if released is None else released
    return scan_main_window(SHAS, _fetch(red), lambda sha: sha in rel)


def _times(age_hours: float) -> dict[str, datetime]:
    return {sha: NOW - timedelta(hours=age_hours) for sha in SHAS}


def test_scan_reports_newest_candidate_not_oldest() -> None:
    scan = _scan(red=frozenset({SHAS[-1]}), released=set())
    assert scan.newest_state == "green_unreleased"
    assert scan.newest_sha == SHAS[0]
    assert scan.deployable_sha is None
    assert "checks failed" not in scan.detail
    assert "green" in scan.detail and SHAS[0][:7] in scan.detail
    assert "no published release" in scan.detail


def test_scan_reports_not_green_for_red_newest() -> None:
    scan = _scan(red=frozenset(SHAS), released=set())
    assert scan.newest_state == "not_green"
    assert scan.newest_sha == SHAS[0]
    assert "not green" in scan.detail and "checks failed" in scan.detail


def test_scan_counts_green_unreleased_ahead_of_release() -> None:
    scan = _scan()
    assert scan.deployable_sha == RELEASED
    assert scan.green_unreleased == tuple(SHAS[:-1])
    assert scan.newest_green_unreleased == SHAS[0]


def test_stale_green_unreleased_main_raises_attention() -> None:
    verdict = assess_release_staleness(_scan(), _times(3.5), NOW, max_age_hours=3, max_green_behind=99)
    assert verdict.attention is True
    assert f"no release published for green main {SHAS[0][:7]}" in verdict.reason
    assert "since 2026-10-25T08:30:00" in verdict.reason


def test_recent_green_unreleased_is_not_attention() -> None:
    verdict = assess_release_staleness(_scan(), _times(1), NOW, max_age_hours=3, max_green_behind=99)
    assert verdict.attention is False


def test_too_many_green_commits_behind_raises_attention() -> None:
    verdict = assess_release_staleness(_scan(), _times(0.1), NOW, max_age_hours=3, max_green_behind=3)
    assert verdict.attention is True
    assert "6 green commit(s) behind" in verdict.reason


def test_nothing_unreleased_is_quiet() -> None:
    scan = _scan(released={SHAS[0]})
    assert assess_release_staleness(scan, _times(50), NOW, max_age_hours=3, max_green_behind=1).attention is False


def test_red_main_is_not_release_staleness() -> None:
    scan = _scan(red=frozenset(SHAS), released=set())
    assert assess_release_staleness(scan, _times(50), NOW, max_age_hours=3, max_green_behind=1).attention is False


def test_unknown_commit_time_does_not_alert_on_age() -> None:
    verdict = assess_release_staleness(_scan(), {}, NOW, max_age_hours=3, max_green_behind=99)
    assert verdict.attention is False


def test_record_event_attention_override() -> None:
    state = AutoDeployState()
    record_event(state, commit="a" * 40, result="skipped", message="m", attention=True)
    assert state.attention is True
    record_event(state, commit="a" * 40, result="skipped", message="m")
    assert state.attention is False


def _cfg(tmp_path: Path, max_green_behind: int = 5) -> CycleConfig:
    deploy = tmp_path / "dashboard"
    deploy.mkdir(exist_ok=True)
    (deploy / "deployment.json").write_text('{"git_sha": "' + RELEASED + '"}', encoding="utf-8")
    return CycleConfig(
        repo_full_name="o/r",
        live_deploy_dir=str(deploy),
        machine_role="hub",
        hub_url=None,
        fleet_token=None,
        max_green_behind=max_green_behind,
    )


def _deps(age_hours: float) -> CycleDeps:
    def gh(path: str) -> Any:
        if path.endswith("/commits/main"):
            return {"sha": SHAS[0]}
        if "commits?sha=main" in path:
            return [
                {"sha": s, "commit": {"committer": {"date": (NOW - timedelta(hours=age_hours)).isoformat()}}}
                for s in SHAS
            ]
        sha = path.split("/commits/")[1].split("/")[0]
        return {"total_count": 1, "check_runs": [_run(sha, "success")]}

    release = {
        "draft": False,
        "target_commitish": RELEASED,
        "assets": [
            {"name": "dashboard-1.0.0.tar.gz", "url": "https://example/tar"},
            {"name": "dashboard-1.0.0.tar.gz.sha256", "url": "https://example/sum"},
        ],
    }
    return CycleDeps(
        github_get=gh,
        list_releases=lambda: [release],
        fetch_fleet_status=lambda: {},
        staff_in_flight=lambda: 0,
        state=AutoDeployState(),
        now=lambda: NOW,
    )


def test_evaluate_cycle_flags_stale_when_already_deployed(tmp_path: Path) -> None:
    deps = _deps(age_hours=5)
    result, message, _ = evaluate_cycle(_cfg(tmp_path), deps)
    assert result == "skipped"
    assert deps.release_staleness is not None and deps.release_staleness.attention is True
    assert f"no release published for green main {SHAS[0][:7]}" in message


def test_evaluate_cycle_quiet_when_fresh(tmp_path: Path) -> None:
    deps = _deps(age_hours=0.5)
    cfg = _cfg(tmp_path, max_green_behind=99)
    result, message, _ = evaluate_cycle(cfg, deps)
    assert result == "skipped"
    assert deps.release_staleness is not None and deps.release_staleness.attention is False
    assert "no release published" not in message
