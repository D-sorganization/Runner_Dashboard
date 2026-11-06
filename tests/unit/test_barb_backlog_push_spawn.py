"""Tests for detached backlog-push spawn (#4098)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import importlib.util

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "backlog_push_spawn", _ROOT / "scripts/barb-worker/backlog-push-spawn.py"
)
assert _spec and _spec.loader
spawn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spawn)


def test_spawn_env_strips_bot_token_from_lane_env() -> None:
    env = spawn._lane_env()
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env


def test_lane_script_refreshes_gh_without_exporting_token() -> None:
    text = (_ROOT / "scripts/barb-worker/backlog-push-lane.sh").read_text(encoding="utf-8")
    assert "unset GH_TOKEN GITHUB_TOKEN" in text
    assert "gh api user" in text
    assert "echo $GH_TOKEN" not in text and 'GH_TOKEN="' not in text


def test_default_launcher_spawn_subprocess_omits_bot_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["env"] = kwargs.get("env")
        class R:
            returncode = 0
            stdout = json.dumps({"supervisor": 1})
            stderr = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("RUNNER_DASHBOARD_ROOT", str(_ROOT))
    monkeypatch.setenv("GH_TOKEN", "gho_test_should_not_leak")
    import barb_backlog_push_relay as relay  # noqa: E402

    cfg = relay.BacklogPushConfig(
        True,
        "http://h",
        "tok",
        frozenset(),
        2,
        tmp_path / "state.json",
        "D/x",
    )
    r = relay.BacklogPushRelay(cfg, github=object(), state={})
    r._default_launcher(
        {"plan": [{"role": "supervisor", "session": "s", "prompt": "fixed prompt"}]},
        tmp_path,
    )
    env = captured.get("env") or {}
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env
