"""Tests for artifact resolution, hub gate, drain wait, and cycle evaluation."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.artifact import pick_release_assets  # noqa: E402
from node_auto_deploy.hub_gate import hub_ready_for_commit  # noqa: E402
from node_auto_deploy.releases import atomic_live_switch, release_dir  # noqa: E402
from node_auto_deploy.runner import CycleConfig, CycleDeps, evaluate_cycle  # noqa: E402
from node_auto_deploy.staff_drain import wait_for_staff_idle  # noqa: E402
from node_auto_deploy.state import AutoDeployState  # noqa: E402

_SHA = "c" * 40
_SHA2 = "d" * 40


def test_pick_release_assets_matches_target_commit() -> None:
    releases = [
        {
            "draft": False,
            "target_commitish": _SHA,
            "assets": [
                {"name": "dashboard-4.10.2.tar.gz", "url": "https://api.github.com/tar"},
                {"name": "dashboard-4.10.2.tar.gz.sha256", "url": "https://api.github.com/sum"},
            ],
        }
    ]
    picked = pick_release_assets(releases, _SHA)
    assert picked is not None
    assert picked["version"] == "4.10.2"


def test_hub_gate_requires_matching_sha() -> None:
    fleet = {
        "DeskComputer": {
            "_role": "hub",
            "deployment": {"git_sha": _SHA},
            "status": "online",
        }
    }
    ok, _ = hub_ready_for_commit(fleet, _SHA)
    assert ok is True
    ok, msg = hub_ready_for_commit(fleet, _SHA2)
    assert ok is False
    assert "want" in msg


def test_wait_for_staff_idle_times_out() -> None:
    calls = {"n": 0}

    def inflight() -> int:
        calls["n"] += 1
        return 1

    ok, msg = wait_for_staff_idle(
        in_flight_count=inflight,
        sleep=lambda _: None,
        timeout_seconds=0.0,
        poll_seconds=0.0,
    )
    assert ok is False
    assert "timed out" in msg


def test_atomic_live_switch_uses_symlink(tmp_path: Path) -> None:
    live = tmp_path / "dashboard"
    rel = release_dir(live, _SHA)
    rel.mkdir(parents=True)
    (rel / "deployment.json").write_text("{}", encoding="utf-8")
    atomic_live_switch(live, rel)
    assert live.is_symlink()
    assert live.resolve() == rel.resolve()


def test_evaluate_cycle_skips_when_ci_red(tmp_path: Path) -> None:
    deploy = tmp_path / "dashboard"
    deploy.mkdir()
    (deploy / "deployment.json").write_text('{"git_sha": "' + ("e" * 40) + '"}', encoding="utf-8")

    def gh(path: str) -> dict:
        if path.endswith("/commits/main"):
            return {"sha": _SHA}
        return {
            "check_runs": [
                {"head_sha": _SHA, "name": "ci", "status": "completed", "conclusion": "failure"},
            ]
        }

    cfg = CycleConfig(
        repo_full_name="D-sorganization/Runner_Dashboard",
        live_deploy_dir=str(deploy),
        machine_role="node",
        hub_url=None,
        fleet_token=None,
    )
    deps = CycleDeps(
        github_get=gh,
        list_releases=lambda: [],
        fetch_fleet_status=lambda: {},
        staff_in_flight=lambda: 0,
        state=AutoDeployState(),
    )
    result, msg, target = evaluate_cycle(cfg, deps)
    assert result == "failed"
    assert target is None
    assert "CI gate" in msg
