"""Unit tests for deploy/node_auto_deploy core (TDD, issue auto-deploy stack)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.ci_gate import ci_gate_for_commit  # noqa: E402
from node_auto_deploy.kill_switch import auto_deploy_disabled, default_flag_path  # noqa: E402
from node_auto_deploy.service_scope import detect_service_scope  # noqa: E402
from node_auto_deploy.state import load_state, record_event, save_state  # noqa: E402
from node_auto_deploy.version import deployed_git_sha  # noqa: E402

_SHA = "a" * 40
_SHA_B = "b" * 40


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
