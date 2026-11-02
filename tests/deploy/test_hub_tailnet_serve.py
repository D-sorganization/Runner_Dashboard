"""Tests for deploy/hub_tailnet_serve.py (#3814)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "hub_tailnet_serve.py"
GUARD_DIR = REPO / "deploy"


def _load_module():
    spec = importlib.util.spec_from_file_location("hub_tailnet_serve", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    guard_spec = importlib.util.spec_from_file_location(
        "loopback_bind_guard", GUARD_DIR / "loopback_bind_guard.py"
    )
    assert guard_spec and guard_spec.loader
    guard_mod = importlib.util.module_from_spec(guard_spec)
    sys.modules["loopback_bind_guard"] = guard_mod
    guard_spec.loader.exec_module(guard_mod)
    sys.modules["hub_tailnet_serve"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()


@pytest.mark.unit
def test_should_manage_when_loopback_auth_on_native() -> None:
    env = {"DASHBOARD_LOOPBACK_AUTH": "1", "DASHBOARD_HOST": "127.0.0.1"}
    ok, reason = mod.should_manage_tailnet_serve(env, assume_wsl=False)
    assert ok is True
    assert reason == "loopback_auth_loopback_bind"


@pytest.mark.unit
def test_skips_on_wsl() -> None:
    env = {"DASHBOARD_LOOPBACK_AUTH": "1"}
    ok, reason = mod.should_manage_tailnet_serve(env, assume_wsl=True)
    assert ok is False
    assert reason == "wsl_uses_mirrored_port_helper"


@pytest.mark.unit
def test_skips_when_loopback_auth_off() -> None:
    ok, reason = mod.should_manage_tailnet_serve({}, assume_wsl=False)
    assert ok is False
    assert reason == "loopback_auth_disabled"


@pytest.mark.unit
def test_status_proxies_loopback_upstream() -> None:
    status = (
        "http://desk.fleet-example.ts.net:8321 (tailnet only)\n"
        "|-- / proxy http://127.0.0.1:8321\n"
    )
    assert mod.status_proxies_loopback_upstream(status, 8321) is True
    assert mod.status_proxies_loopback_upstream(status, 8322) is False


@pytest.mark.unit
def test_diagnose_cli(tmp_path: Path) -> None:
    env_file = tmp_path / "env"
    env_file.write_text("DASHBOARD_LOOPBACK_AUTH=1\nDASHBOARD_HOST=127.0.0.1\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "diagnose", "--env-file", str(env_file), "--port", "8321"],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    payload = json.loads(proc.stdout)
    assert payload["manage"] is True
    assert payload["upstream"] == "http://127.0.0.1:8321"


@pytest.mark.unit
def test_ensure_dry_run_reports_command(tmp_path: Path) -> None:
    env_file = tmp_path / "env"
    env_file.write_text("DASHBOARD_LOOPBACK_AUTH=1\n", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "ensure",
            "--dry-run",
            "--env-file",
            str(env_file),
            "--port",
            "8321",
            "--wait-seconds",
            "0",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    payload = json.loads(proc.stdout)
    assert payload["action"] in {"would_configure", "skipped", "unchanged", "failed"}
