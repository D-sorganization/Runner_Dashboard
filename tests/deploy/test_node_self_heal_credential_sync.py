"""Behavioral tests for deploy/node-self-heal.sh's credential-sync health reporting (RD #3975).

Desk observed the deployed sync_provider_credentials.py crash while node-self-heal.sh kept
reporting ``healthy: true`` in its status file, because the script only ever checked HTTP
health and silently discarded a non-zero/raising sync exit. These tests isolate section 1
(credential sync) by copying the script into an empty directory with stub ``curl``/
``systemctl``/``sudo`` functions injected, so sections 2-4 (release pruning, HTTP restart,
disk guard) never touch the real host.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SELF_HEAL = DEPLOY / "node-self-heal.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)

_OK_SYNC_SCRIPT = """\
import json
print(json.dumps({"ok": True, "synced": {"claude": ["/x"]}}))
"""

_FAILING_SYNC_SCRIPT = """\
import json, sys
print(json.dumps({"ok": False, "error": "PermissionError: denied"}))
sys.exit(1)
"""


def _isolated_script(tmp_path: Path) -> Path:
    """Copy node-self-heal.sh into tmp_path with no-op curl/systemctl/sudo, no real-host side effects."""
    text = SELF_HEAL.read_text(encoding="utf-8")
    stubs = "\n".join(
        [
            "curl() { return 1; }",
            "systemctl() { return 1; }",
            "sudo() { return 1; }",
        ]
    )
    assert "set -euo pipefail" in text
    text = text.replace("set -euo pipefail", "set -euo pipefail\n" + stubs, 1)
    script = tmp_path / "node-self-heal.sh"
    script.write_text(text, encoding="utf-8", newline="\n")
    script.chmod(0o755)
    return script


def _run_self_heal(tmp_path: Path, sync_script_body: str) -> dict:
    script = _isolated_script(tmp_path)
    (tmp_path / "sync_provider_credentials.py").write_text(sync_script_body, encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()
    status_file = home / ".config" / "runner-dashboard" / "self-heal-status.json"

    env = os.environ | {
        "HOME": str(home),
        "RUNNER_BASE_DIR": str(tmp_path / "does-not-exist"),
        "DASHBOARD_PORT": "18321",
    }
    args = [BASH, as_bash_path(script)]
    res = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 0, f"self-heal exited non-zero: {res.stderr}"
    assert status_file.is_file(), f"status file never written: {res.stderr}"
    return json.loads(status_file.read_text(encoding="utf-8"))


def test_healthy_reports_true_when_sync_succeeds(tmp_path: Path) -> None:
    status = _run_self_heal(tmp_path, _OK_SYNC_SCRIPT)
    assert status["credential_sync_ok"] is True
    assert status["credential_sync_error"] is None
    assert status["healthy"] is False  # curl is stubbed to fail, so http_healthy stays false
    assert status["http_healthy"] is False


def test_credential_sync_failure_is_not_reported_healthy(tmp_path: Path) -> None:
    """The exact RD #3975 bug: a crashing/non-zero sync script must flip credential_sync_ok off."""
    status = _run_self_heal(tmp_path, _FAILING_SYNC_SCRIPT)
    assert status["credential_sync_ok"] is False
    assert status["healthy"] is False
    assert "alert:credential_sync_failed" in status["actions"]
    assert status["credential_sync_error"]
    assert "denied" in status["credential_sync_error"] or "PermissionError" in status["credential_sync_error"]


def test_sync_script_staleness_is_flagged_against_canonical_deployed_copy(tmp_path: Path) -> None:
    """A self-heal-cached sync script that drifted from the live deploy/ copy must be flagged."""
    script = _isolated_script(tmp_path)
    (tmp_path / "sync_provider_credentials.py").write_text(_OK_SYNC_SCRIPT, encoding="utf-8")

    deploy_dir = tmp_path / "live-deploy"
    (deploy_dir / "deploy").mkdir(parents=True)
    (deploy_dir / "deploy" / "sync_provider_credentials.py").write_text(
        _OK_SYNC_SCRIPT + "# canonical copy differs\n", encoding="utf-8"
    )

    home = tmp_path / "home"
    home.mkdir()
    status_file = home / ".config" / "runner-dashboard" / "self-heal-status.json"
    env = os.environ | {
        "HOME": str(home),
        "RUNNER_BASE_DIR": str(tmp_path / "does-not-exist"),
        "RUNNER_DASHBOARD_DEPLOY_DIR": str(deploy_dir),
        "DASHBOARD_PORT": "18321",
    }
    res = subprocess.run([BASH, as_bash_path(script)], capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 0, f"self-heal exited non-zero: {res.stderr}"
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert "alert:sync_script_stale" in status["actions"]


def test_no_staleness_alert_when_deployed_copy_matches(tmp_path: Path) -> None:
    script = _isolated_script(tmp_path)
    (tmp_path / "sync_provider_credentials.py").write_text(_OK_SYNC_SCRIPT, encoding="utf-8")

    deploy_dir = tmp_path / "live-deploy"
    (deploy_dir / "deploy").mkdir(parents=True)
    (deploy_dir / "deploy" / "sync_provider_credentials.py").write_text(_OK_SYNC_SCRIPT, encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()
    status_file = home / ".config" / "runner-dashboard" / "self-heal-status.json"
    env = os.environ | {
        "HOME": str(home),
        "RUNNER_BASE_DIR": str(tmp_path / "does-not-exist"),
        "RUNNER_DASHBOARD_DEPLOY_DIR": str(deploy_dir),
        "DASHBOARD_PORT": "18321",
    }
    res = subprocess.run([BASH, as_bash_path(script)], capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 0, f"self-heal exited non-zero: {res.stderr}"
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert "alert:sync_script_stale" not in status["actions"]
