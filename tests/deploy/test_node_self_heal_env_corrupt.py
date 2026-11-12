"""Self-heal must probe /health and must not restart-loop on a corrupt env file."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SELF_HEAL = DEPLOY / "node-self-heal.sh"
ENV_CHECK = DEPLOY / "runner_dashboard_env_check.py"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)

_OK_SYNC = 'import json\nprint(json.dumps({"ok": True, "synced": {}}))\n'


def _run(tmp_path: Path, *, env_bytes: bytes, http_ok: bool) -> tuple[dict, str]:
    text = SELF_HEAL.read_text(encoding="utf-8")
    curl_stub = (
        'curl() { case "$*" in *"/health"*) return 0;; esac; return 1; }'
        if http_ok
        else 'curl() { echo "$*" >&2; return 1; }'
    )
    stubs = "\n".join(
        [
            curl_stub,
            'systemctl() { if [[ "$1" == "restart" ]]; then echo "WOULD_RESTART" >&2; fi; return 1; }',
            "sudo() { return 1; }",
        ]
    )
    text = text.replace("set -euo pipefail", "set -euo pipefail\n" + stubs, 1)
    script = tmp_path / "node-self-heal.sh"
    script.write_text(text, encoding="utf-8", newline="\n")
    script.chmod(0o755)

    (tmp_path / "sync_provider_credentials.py").write_text(_OK_SYNC, encoding="utf-8")
    (tmp_path / "runner_dashboard_env_check.py").write_bytes(ENV_CHECK.read_bytes())

    home = tmp_path / "home"
    env_dir = home / ".config" / "runner-dashboard"
    env_dir.mkdir(parents=True)
    (env_dir / "env").write_bytes(env_bytes)

    env = os.environ | {
        "HOME": str(home),
        "RUNNER_BASE_DIR": str(tmp_path / "missing"),
        "DASHBOARD_PORT": "18321",
    }
    res = subprocess.run([BASH, as_bash_path(script)], capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 0, res.stderr
    status = json.loads((env_dir / "self-heal-status.json").read_text(encoding="utf-8"))
    return status, res.stderr


def test_self_heal_probes_health_not_version(tmp_path: Path) -> None:
    status, stderr = _run(tmp_path, env_bytes=b"FLEET_NODES=x\n", http_ok=True)
    assert status["http_healthy"] is True
    assert status["env_file_ok"] is True
    assert "/health" in stderr or status["http_healthy"]


def test_corrupt_env_skips_service_restart(tmp_path: Path) -> None:
    status, stderr = _run(
        tmp_path,
        env_bytes=b"FLEET_NODES=hub\n" + b"\x00" * 40 + b"GH_TOKEN=fixture-old\n",
        http_ok=False,
    )
    assert status["env_file_ok"] is False
    assert "alert:env_file_corrupt" in status["actions"]
    assert "WOULD_RESTART" not in stderr
    assert status["healthy"] is False
