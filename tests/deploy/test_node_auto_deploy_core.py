"""Unit tests for deploy/node_auto_deploy core (TDD, issue auto-deploy stack)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.ci_gate import ci_gate_for_commit  # noqa: E402
from node_auto_deploy.kill_switch import auto_deploy_disabled  # noqa: E402
from node_auto_deploy.service_scope import detect_service_scope  # noqa: E402
from node_auto_deploy.state import load_state, record_event, save_state  # noqa: E402
from node_auto_deploy.version import deployed_git_sha  # noqa: E402

_SHA = "a" * 40
_SHA_B = "b" * 40
_ECB3F6B_SHA = "ecb3f6bce90e28d81d5f5b23d240b382bc9bbfd2"  # pragma: allowlist secret
_ECB3F6B_FIXTURE = REPO / "tests" / "deploy" / "fixtures" / "ecb3f6b_check_runs.json"


def test_deployed_git_sha_reads_deployment_json(tmp_path: Path) -> None:
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps({"git_sha": _SHA}), encoding="utf-8")
    assert deployed_git_sha(path) == _SHA
    assert deployed_git_sha(tmp_path / "missing.json") is None


def test_ci_gate_requires_completed_success() -> None:
    runs = [
        {"head_sha": _SHA, "name": "ci", "status": "completed", "conclusion": "success"},
        {"head_sha": _SHA, "name": "lint", "status": "completed", "conclusion": "success"},
    ]
    ok, detail = ci_gate_for_commit(runs, _SHA)
    assert ok is True
    assert "succeeded" in detail

    pending = [{"head_sha": _SHA, "name": "ci", "status": "in_progress", "conclusion": None}]
    ok, _ = ci_gate_for_commit(pending, _SHA)
    assert ok is False

    failed = [{"head_sha": _SHA, "name": "ci", "status": "completed", "conclusion": "failure"}]
    ok, detail = ci_gate_for_commit(failed, _SHA)
    assert ok is False
    assert "failed" in detail


def test_ci_gate_treats_skipped_and_duplicate_names_as_passing() -> None:
    skipped_only = [
        {"head_sha": _SHA, "name": "guard", "status": "completed", "conclusion": "skipped"},
    ]
    ok, _ = ci_gate_for_commit(skipped_only, _SHA)
    assert ok is True

    tier_gated_with_success = [
        {"head_sha": _SHA, "name": "lint", "status": "completed", "conclusion": "skipped"},
        {"head_sha": _SHA, "name": "lint", "status": "completed", "conclusion": "success"},
    ]
    ok, _ = ci_gate_for_commit(tier_gated_with_success, _SHA)
    assert ok is True

    skipped_plus_failure = [
        {"head_sha": _SHA, "name": "lint", "status": "completed", "conclusion": "skipped"},
        {"head_sha": _SHA, "name": "lint", "status": "completed", "conclusion": "failure"},
    ]
    ok, detail = ci_gate_for_commit(skipped_plus_failure, _SHA)
    assert ok is False
    assert "lint" in detail

    neutral_only = [
        {"head_sha": _SHA, "name": "note", "status": "completed", "conclusion": "neutral"},
    ]
    ok, _ = ci_gate_for_commit(neutral_only, _SHA)
    assert ok is False


def test_ci_gate_passes_ecb3f6b_main_check_list() -> None:
    payload = json.loads(_ECB3F6B_FIXTURE.read_text(encoding="utf-8"))
    assert "ecb3f6b" in payload["fetched_from"]
    runs = [{**run, "head_sha": _ECB3F6B_SHA} for run in payload["check_runs"]]
    assert len(runs) == 29
    conclusions = {str(r.get("conclusion")) for r in runs}
    assert conclusions == {"success", "skipped"}
    assert sum(1 for r in runs if r.get("conclusion") == "success") == 25
    assert sum(1 for r in runs if r.get("conclusion") == "skipped") == 4

    ok, detail = ci_gate_for_commit(runs, _ECB3F6B_SHA)
    assert ok is True
    assert _ECB3F6B_SHA[:7] in detail


def test_ci_gate_rejects_malformed_inputs() -> None:
    ok, detail = ci_gate_for_commit("not-a-list", _SHA)  # type: ignore[arg-type]
    assert ok is False
    assert "list" in detail

    ok, detail = ci_gate_for_commit([{"name": "ci", "head_sha": _SHA}], _SHA)
    assert ok is False
    assert "invalid check run" in detail


def test_service_scope_prefers_system_when_both_active() -> None:
    decision = detect_service_scope([("system", 0), ("user", 0)])
    assert decision is not None
    assert decision.scope == "system"
    assert decision.restart_argv()[0] == "sudo"

    user_only = detect_service_scope([("system", 3), ("user", 0)])
    assert user_only is not None
    assert user_only.scope == "user"
    assert user_only.restart_argv() == ["systemctl", "--user", "restart", "runner-dashboard"]


def test_kill_switch_env_and_flag(tmp_path: Path) -> None:
    flag = tmp_path / "auto-deploy.disabled"
    assert auto_deploy_disabled(env={"RUNNER_DASHBOARD_AUTO_DEPLOY": "1"}, flag_file=flag) is False
    assert auto_deploy_disabled(env={"RUNNER_DASHBOARD_AUTO_DEPLOY": "0"}, flag_file=flag) is True
    flag.write_text("", encoding="utf-8")
    assert auto_deploy_disabled(env={"RUNNER_DASHBOARD_AUTO_DEPLOY": "1"}, flag_file=flag) is True


def test_state_records_attention_on_rollback(tmp_path: Path) -> None:
    path = tmp_path / "auto_deploy_state.json"
    state = load_state(path)
    fixed = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    record_event(
        state,
        commit=_SHA,
        result="rollback",
        message="health check failed",
        rolled_back_from=_SHA_B,
        now=fixed,
    )
    save_state(path, state)
    reloaded = load_state(path)
    assert reloaded.attention is True
    assert reloaded.last_result == "rollback"
    assert reloaded.events[0]["rolled_back_from"] == _SHA_B
