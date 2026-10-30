"""CI regression suite for node auto-deploy live bugs (#2083, #2108, #2111, #2114).

Would have failed on pre-fix code: HOME-unbound install paths, system Python 3.10
imports, hub gate hitting /api/fleet/status, CI gate treating skipped checks as failed.
Validated with ``pytest --confcutdir=tests/deploy`` so the suite stays deploy-only.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from loader_env import loader_env  # resolved via tests/deploy/conftest.py, immune to a shadowing `tests` package

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"
BASH = os.environ.get("BASH", "/bin/bash")
INSTALL_SH = DEPLOY / "install-node-auto-deploy.sh"
TIMER = DEPLOY / "systemd" / "runner-dashboard-auto-deploy.timer"
_ECB3F6B_SHA = "ecb3f6bce90e28d81d5f5b23d240b382bc9bbfd2"  # pragma: allowlist secret
_FIXTURE = REPO / "tests" / "deploy" / "fixtures" / "ecb3f6b_check_runs.json"


def test_auto_deploy_queue_guard_does_not_leave_non_cone_sparse_state() -> None:
    """The guard must not poison the next job's full checkout on a reused runner."""
    import yaml

    workflow = yaml.safe_load((REPO / ".github/workflows/ci-auto-deploy-regression.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["queue-entry-alive"]["steps"]
    checkout = next(step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@"))
    options = checkout.get("with", {})
    assert options.get("sparse-checkout-cone-mode") is not False
    assert options.get("sparse-checkout") == ".github/scripts"

sys.path.insert(0, str(DEPLOY))
from node_auto_deploy.ci_gate import ci_gate_for_commit, parse_check_runs_payload  # noqa: E402
from node_auto_deploy.hub_gate import fetch_fleet_status  # noqa: E402


def _parse_unit_flat(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _bash_source_install(cmd: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    script = f'# shellcheck disable=SC1091\nsource "{INSTALL_SH}"\n{cmd}'
    return subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=merged,
    )


@pytest.mark.skipif(not INSTALL_SH.is_file(), reason="install script missing")
@pytest.mark.skipif(not Path(BASH).exists(), reason="bash required")
def test_rendered_systemd_units_validate_in_temp_root(tmp_path: Path) -> None:
    """Render system + user units like install-node-auto-deploy.sh and verify them."""
    deploy_dir = tmp_path / "dashboard"
    exec_stub = deploy_dir / "deploy" / "node-auto-deploy.sh"
    exec_stub.parent.mkdir(parents=True)
    exec_stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    os.chmod(exec_stub, 0o755)
    env_file = tmp_path / "env"
    env_file.write_text("GH_TOKEN=test\n", encoding="utf-8")
    python_bin = shutil.which("python3") or sys.executable
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()

    proc = _bash_source_install(
        f"""
auto_deploy_bind_paths
render_service_unit system "{python_bin}" > "{unit_dir}/system.service"
render_service_unit user "{python_bin}" > "{unit_dir}/user.service"
""",
        env={
            "RUNNER_DASHBOARD_USER": "ciuser",
            "RUNNER_DASHBOARD_DEPLOY_DIR": str(deploy_dir),
            "RUNNER_DASHBOARD_ENV_FILE": str(env_file),
            "HOME": "/home/ciuser",
        },
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout

    system_unit = unit_dir / "system.service"
    user_unit = unit_dir / "user.service"
    system_text = system_unit.read_text(encoding="utf-8")
    user_text = user_unit.read_text(encoding="utf-8")

    assert "User=ciuser" in system_text
    assert "User=" not in user_text
    for text in (system_text, user_text):
        assert "Environment=HOME=/home/ciuser" in text
        assert f"Environment=RUNNER_DASHBOARD_PYTHON={python_bin}" in text
        assert f"ExecStart={deploy_dir}/deploy/node-auto-deploy.sh" in text

    shutil.copy(TIMER, unit_dir / "runner-dashboard-auto-deploy.timer")

    analyze = shutil.which("systemd-analyze")
    if analyze:
        verify = subprocess.run(
            [analyze, "verify", str(system_unit), str(user_unit), str(unit_dir / "runner-dashboard-auto-deploy.timer")],
            capture_output=True,
            text=True,
            check=False,
        )
        assert verify.returncode == 0, verify.stderr or verify.stdout
    else:
        for name, text in (("system", system_text), ("user", user_text)):
            cfg = _parse_unit_flat(unit_dir / f"{name}.service")
            assert cfg.get("Type") == "oneshot"
            assert cfg.get("ExecStart", "").endswith("node-auto-deploy.sh")
            assert "RUNNER_DASHBOARD_AUTO_DEPLOY=1" in text


@pytest.mark.skipif(not Path(BASH).exists(), reason="bash required")
def test_entrypoint_runs_under_systemd_like_env_home_unset(tmp_path: Path) -> None:
    """Regression #2083: system oneshot services often have no HOME."""
    deploy = tmp_path / "dashboard"
    deploy.mkdir()
    script = deploy / "deploy" / "node-auto-deploy.sh"
    script.parent.mkdir(parents=True)
    script.write_text((DEPLOY / "node-auto-deploy.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (deploy / "deploy" / "auto-deploy-env.sh").write_text(
        (DEPLOY / "auto-deploy-env.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (deploy / "deploy" / "resolve-auto-deploy-python.sh").write_text(
        (DEPLOY / "resolve-auto-deploy-python.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    venv_python = deploy / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(sys.executable)
    stub = deploy / "deploy" / "node-auto-deploy.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    os.chmod(script, 0o755)

    env = {
        "PATH": os.environ.get("PATH", ""),
        "RUNNER_DASHBOARD_USER": "ciuser",
        "RUNNER_DASHBOARD_DEPLOY_DIR": str(deploy),
        "RUNNER_DASHBOARD_ENV_FILE": str(tmp_path / "missing.env"),
        "RUNNER_DASHBOARD_PYTHON": str(venv_python),
        # Not scrubbed state: where the shared-libpython interpreter finds its library.
        **loader_env(),
    }
    proc = subprocess.run([BASH, str(script)], env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def _ecb3f6b_runs() -> list[dict[str, object]]:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        raw = payload.get("check_runs", [])
    else:
        raw = payload
    return [{**run, "head_sha": _ECB3F6B_SHA} for run in raw]


def test_ci_gate_passes_recorded_ecb3f6b_check_runs() -> None:
    runs = _ecb3f6b_runs()
    assert len(runs) == 29
    conclusions = Counter(str(r.get("conclusion")) for r in runs)
    assert conclusions["success"] == 25
    assert conclusions["skipped"] == 4
    dup_names = [name for name, count in Counter(str(r.get("name")) for r in runs).items() if count > 1]
    assert dup_names, "fixture must include duplicate check names (tier-gated skips)"

    ok, detail = ci_gate_for_commit(runs, _ECB3F6B_SHA)
    assert ok is True
    assert _ECB3F6B_SHA[:7] in detail

    payload = {"check_runs": runs}
    ok2, _ = ci_gate_for_commit(parse_check_runs_payload(payload), _ECB3F6B_SHA)
    assert ok2 is True


def test_ci_gate_blocks_failure_conclusion_even_with_skipped_duplicate() -> None:
    runs = _ecb3f6b_runs()
    runs.append(
        {
            "head_sha": _ECB3F6B_SHA,
            "name": "tests",
            "status": "completed",
            "conclusion": "failure",
        }
    )
    ok, detail = ci_gate_for_commit(runs, _ECB3F6B_SHA)
    assert ok is False
    assert "tests" in detail


@pytest.mark.parametrize("python_exe", ["python3.10", "python3"])
def test_node_auto_deploy_package_imports_on_hub_and_project_python(python_exe: str) -> None:
    """Regression #2108: package must import on WSL system 3.10 and project interpreter."""
    exe = shutil.which(python_exe)
    if exe is None:
        pytest.skip(f"{python_exe} not on PATH")
    proc = subprocess.run(
        [
            exe,
            "-c",
            "import sys; sys.path.insert(0, 'deploy'); "
            "import node_auto_deploy.ci_gate, node_auto_deploy.hub_gate, node_auto_deploy.state; "
            "from node_auto_deploy.state import record_event, load_state; "
            "from pathlib import Path; from datetime import timezone, datetime; "
            "s=load_state(Path('/tmp/auto_deploy_smoke.json')); "
            "record_event(s, commit='a'*40, result='skipped', message='ok', "
            "now=datetime(2026,1,1,tzinfo=timezone.utc))",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    if python_exe == "python3":
        major, minor = sys.version_info[:2]
        assert (major, minor) >= (3, 11)


def test_hub_rollout_probe_uses_lightweight_version_not_fleet_status() -> None:
    """Regression #2111: hub gate must not fan out /api/fleet/status during rollout."""
    hub_src = (DEPLOY / "node_auto_deploy" / "hub_gate.py").read_text(encoding="utf-8")
    fetch_body = hub_src.split("def fetch_fleet_status", 1)[1].split("\ndef ", 1)[0]
    assert "fetch_hub_version" in fetch_body
    assert re.search(r'url\s*=\s*f"[^"]*fleet/status', fetch_body) is None
    assert 'url = f"{hub_url.rstrip' in hub_src and "/api/version" in hub_src

    deploy_py = (DEPLOY / "node-auto-deploy.py").read_text(encoding="utf-8")
    assert "fleet/status" not in deploy_py
    assert "wait_for_health" in deploy_py
    assert "probe_health" in (DEPLOY / "node_auto_deploy" / "health.py").read_text(encoding="utf-8")

    lib = (DEPLOY / "lib.sh").read_text(encoding="utf-8")
    match = re.search(r"DEPLOY_HEALTH_DELAYS:-([0-9 ]+)", lib)
    assert match is not None
    delays = [int(x) for x in match.group(1).split()]
    assert sum(delays) >= 45


def test_fetch_fleet_status_reads_version_endpoint() -> None:
    from unittest.mock import patch

    version_body = json.dumps({"git_sha": "a" * 40, "dashboard": "4.0.0"}).encode()

    class _Resp:
        def read(self) -> bytes:
            return version_body

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_a: object) -> bool:
            return False

    with patch("node_auto_deploy.hub_gate.urlopen", return_value=_Resp()) as mock_open:
        payload = fetch_fleet_status("http://hub:8321", "token")

    req = mock_open.call_args[0][0]
    assert req.full_url.endswith("/api/version")
    assert payload["hub"]["deployment"]["git_sha"] == "a" * 40


def test_auto_deploy_sources_avoid_datetime_utc_syntax() -> None:
    """AST guard: datetime.UTC breaks on hub system Python 3.10."""
    pkg = DEPLOY / "node_auto_deploy"
    offenders: list[str] = []
    for path in sorted(pkg.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "datetime":
                for alias in node.names:
                    if alias.name == "UTC":
                        offenders.append(f"{path.relative_to(REPO)}: import UTC")
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "datetime" and node.attr == "UTC":
                    offenders.append(f"{path.relative_to(REPO)}: datetime.UTC")
    assert offenders == []
