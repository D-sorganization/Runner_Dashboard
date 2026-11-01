"""Tests for scripts/barb-worker/bootstrap.sh (#3658)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "scripts" / "barb-worker"
BOOTSTRAP = SCRIPT_DIR / "bootstrap.sh"
LANE = SCRIPT_DIR / "lane-loop.sh"
WITH_ENV = SCRIPT_DIR / "with-fleet-env.sh"


def test_scripts_exist_and_are_executable() -> None:
    for path in (BOOTSTRAP, LANE, WITH_ENV):
        assert path.is_file(), path
        assert path.stat().st_mode & 0o111, f"{path} must be executable"


def test_bootstrap_documents_subcommands() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "verify" in text
    assert "barb-grok" in text and "barb-dots" in text and "barb-duet" in text
    assert "1046" in text and "1056" in text and "1066" in text
    assert "hub-a.fleet-example.ts.net" in text


def test_bootstrap_never_echoes_token() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "echo \"${FLEET_API_TOKEN" not in text
    assert "echo \"${token}" not in text and "echo ${token}" not in text
    assert "printf '%s' \"${token}\"" not in text
    assert "FLEET_API_TOKEN" in text


def test_hub_gh_hook_marked() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "install-hub-fleet-gh.sh" in text
    assert "HOOK" in text or "hub-minted" in text


def test_bootstrap_shared_runner_dashboard_and_order() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "/opt/Runner_Dashboard" in text or "BARB_RUNNER_DASHBOARD_ROOT" in text
    assert "clone_shared_runner_dashboard" in text
    provision = text.split("provision_user()", 1)[1].split("cmd_install()", 1)[0]
    gh_pos = provision.index("install_user_github_wrappers")
    clone_pos = provision.index("clone_fleet_repos_for_user")
    assert gh_pos > clone_pos, "gh wrapper must run after per-user fleet clone in provision_user"
    install = text.split("cmd_install()", 1)[1].split("verify_lane()", 1)[0]
    assert install.index("clone_shared_runner_dashboard") < install.index("provision_user")


def test_bootstrap_user_systemd_linger_and_runtime_dir() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "loginctl enable-linger" in text
    assert "XDG_RUNTIME_DIR" in text
    assert "run_as_user_systemd" in text
    assert '2>/dev/null || true' not in text.split("enable-linger")[1].split("\n", 1)[0]


def test_bootstrap_installs_node22_when_needed() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "install_nodejs_for_claude_code" in text
    assert "setup_22.x" in text
    assert "nodejs_major_version" in text


def test_lane_loop_uses_barb_cli() -> None:
    text = LANE.read_text(encoding="utf-8")
    assert "barb_cli.py inbox" in text
    assert "with-fleet-env.sh" in text


def test_with_fleet_env_reads_token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    token_path = cfg / "token"
    token_path.write_text("lane-test-token\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    # Point RD config at tmp token via HOME override in with-fleet-env (uses ~/.config/runner-dashboard)
    rd = tmp_path / ".config" / "runner-dashboard"
    rd.mkdir(parents=True)
    (rd / "token").write_text("lane-test-token\n", encoding="utf-8")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash required")
    proc = subprocess.run(
        [bash, "-c", f'source "{WITH_ENV}"; printf "%s" "${{FLEET_API_TOKEN:-}}"'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "lane-test-token"


def test_verify_output_format_static() -> None:
    """verify_lane printf shape is stable for operators."""
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert re.search(r"livez=%s staff_summary=%s", text)


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_clean() -> None:
    proc = subprocess.run(
        ["shellcheck", "-x", str(BOOTSTRAP), str(LANE), str(WITH_ENV)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
