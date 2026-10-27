"""The Playwright e2e backend must not touch the runner user's real state (#2943)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts import start_e2e_backend as e2e

REAL_ENV = {
    "HOME": "/home/runner-user",
    "XDG_CONFIG_HOME": "/home/runner-user/.config",
    "PATH": "/usr/bin",
    "MACHINE_REGISTRY_PATH": "/home/runner-user/registry.yml",
    "FLEET_NODES": "hub=http://example.invalid:8321",
}


def test_env_isolates_home_and_config(tmp_path: Path) -> None:
    env = e2e.build_backend_env(REAL_ENV, tmp_path, port=4321)
    home = Path(env["HOME"])
    assert home != Path(REAL_ENV["HOME"])
    assert tmp_path in home.parents
    assert Path(env["XDG_CONFIG_HOME"]).is_relative_to(tmp_path)
    assert Path(env["XDG_CACHE_HOME"]).is_relative_to(tmp_path)
    assert Path(env["RUNNER_DASHBOARD_SESSION_SECRET_DIR"]).is_relative_to(tmp_path)


def test_env_disables_registry_and_fleet(tmp_path: Path) -> None:
    env = e2e.build_backend_env(REAL_ENV, tmp_path, port=4321)
    assert env["AUTODERIVE_FLEET_NODES"] == "0"
    assert "FLEET_NODES" not in env
    assert "MACHINE_REGISTRY_PATH" not in env


def test_env_uses_temp_leader_lock(tmp_path: Path) -> None:
    env = e2e.build_backend_env(REAL_ENV, tmp_path, port=4321)
    assert Path(env["DASHBOARD_LEADER_LOCK_PATH"]).is_relative_to(tmp_path)


def test_env_keeps_test_settings_and_path(tmp_path: Path) -> None:
    env = e2e.build_backend_env(REAL_ENV, tmp_path, port=4321)
    assert env["DASHBOARD_PORT"] == "4321"
    assert env["DASHBOARD_LOOPBACK_AUTH"] == "1"
    assert env["GH_TOKEN"] == "dummy"
    assert env["PATH"] == REAL_ENV["PATH"]


def test_env_rejects_bad_port(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        e2e.build_backend_env(REAL_ENV, tmp_path, port=0)


def test_readiness_budget_is_at_least_90s() -> None:
    assert e2e.READY_TIMEOUT_S >= 90


def test_tail_returns_last_lines(tmp_path: Path) -> None:
    log = tmp_path / "b.log"
    log.write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
    tail = e2e.tail_log(log, lines=3)
    assert tail.splitlines() == ["line97", "line98", "line99"]
    assert e2e.tail_log(tmp_path / "missing.log").startswith("<no backend log")


def test_wait_ready_dumps_log_tail_on_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "b.log"
    log.write_text("boom: stuck in startup\n", encoding="utf-8")
    ok = e2e.wait_ready(port=1, log_path=log, timeout_s=0, probe=lambda _url: False, sleep=lambda _s: None)
    assert ok is False
    assert "boom: stuck in startup" in capsys.readouterr().err


def test_wait_ready_succeeds_when_probe_passes(tmp_path: Path) -> None:
    calls = iter([False, False, True])
    ok = e2e.wait_ready(
        port=1, log_path=tmp_path / "x", timeout_s=10, probe=lambda _u: next(calls), sleep=lambda _s: None
    )
    assert ok is True


def test_module_importable_without_side_effects() -> None:
    assert "start_e2e_backend" in sys.modules["scripts.start_e2e_backend"].__name__
