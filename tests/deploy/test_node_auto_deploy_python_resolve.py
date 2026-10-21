"""Python interpreter resolution and 3.10-safe imports for node auto-deploy."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"
BASH = os.environ.get("BASH", "/bin/bash")
RESOLVE_SH = DEPLOY / "resolve-auto-deploy-python.sh"


def _bash_source(cmd: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [BASH, "-c", f'source "{RESOLVE_SH}"; {cmd}'],
        capture_output=True,
        text=True,
        check=False,
        env=merged,
    )


@pytest.mark.skipif(not RESOLVE_SH.is_file(), reason="resolve-auto-deploy-python.sh missing")
@pytest.mark.skipif(not Path(BASH).exists(), reason="bash required")
def test_resolve_prefers_deploy_venv(tmp_path: Path) -> None:
    deploy = tmp_path / "dashboard"
    venv_python = deploy / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(sys.executable)
    proc = _bash_source(
        f'resolve_auto_deploy_python "{deploy}" system',
        env={"RUNNER_DASHBOARD_PYTHON": ""},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(venv_python)


@pytest.mark.skipif(not RESOLVE_SH.is_file(), reason="resolve-auto-deploy-python.sh missing")
@pytest.mark.skipif(not Path(BASH).exists(), reason="bash required")
def test_resolve_honors_runner_dashboard_python_env(tmp_path: Path) -> None:
    deploy = tmp_path / "dashboard"
    deploy.mkdir()
    custom = tmp_path / "custom-python"
    custom.symlink_to(sys.executable)
    proc = _bash_source(
        f'resolve_auto_deploy_python "{deploy}" system',
        env={"RUNNER_DASHBOARD_PYTHON": str(custom)},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(custom)


@pytest.mark.skipif(not RESOLVE_SH.is_file(), reason="resolve-auto-deploy-python.sh missing")
@pytest.mark.skipif(not Path(BASH).exists(), reason="bash required")
def test_require_python_accepts_current_interpreter() -> None:
    proc = _bash_source(f'require_auto_deploy_python_version "{sys.executable}"')
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_node_auto_deploy_package_does_not_import_datetime_utc() -> None:
    """Auto-deploy must run on the dashboard venv; modules stay 3.10-parseable."""
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


def test_install_template_records_dashboard_python() -> None:
    unit = (DEPLOY / "systemd" / "runner-dashboard-auto-deploy.service").read_text(encoding="utf-8")
    install = (DEPLOY / "install-node-auto-deploy.sh").read_text(encoding="utf-8")
    assert "RUNNER_DASHBOARD_PYTHON" in unit
    assert "@PYTHON_BIN@" in unit
    assert "@PYTHON_BIN@" in install or "PYTHON_BIN" in install


def test_node_auto_deploy_modules_import_under_current_python() -> None:
    sys.path.insert(0, str(DEPLOY))
    import node_auto_deploy.state  # noqa: F401, PLC0415

