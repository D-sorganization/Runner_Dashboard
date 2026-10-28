"""setup.sh systemd scope: user vs system install without duplicates (#2058)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.deploy.bash_host import BASH, as_bash_path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCOPE_LIB = REPO_ROOT / "deploy" / "setup-systemd-scope.sh"
SETUP_SH = REPO_ROOT / "deploy" / "setup.sh"


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_resolve_scope_uses_user_when_user_unit_present(tmp_path: Path) -> None:
    user_unit = tmp_path / ".config" / "systemd" / "user" / "runner-dashboard.service"
    user_unit.parent.mkdir(parents=True)
    user_unit.write_text("[Service]\n", encoding="utf-8")
    script = f"""
set -euo pipefail
export HOME={as_bash_path(tmp_path)}
source {as_bash_path(SCOPE_LIB)}
resolve_systemd_dashboard_scope "" ""
printf '%s' "$SYSTEMD_DASHBOARD_SCOPE"
"""
    out = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "user"


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_system_service_flag_rejects_existing_user_unit(tmp_path: Path) -> None:
    user_unit = tmp_path / ".config" / "systemd" / "user" / "runner-dashboard.service"
    user_unit.parent.mkdir(parents=True)
    user_unit.write_text("[Service]\n", encoding="utf-8")
    script = f"""
set -euo pipefail
export HOME={as_bash_path(tmp_path)}
source {as_bash_path(SCOPE_LIB)}
if resolve_systemd_dashboard_scope "" "1"; then
  exit 0
fi
exit 1
"""
    out = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert out.returncode != 0
    assert "user unit" in (out.stderr + out.stdout).lower()


def test_setup_sh_sources_systemd_scope_lib() -> None:
    text = SETUP_SH.read_text(encoding="utf-8")
    assert "setup-systemd-scope.sh" in text
    assert "--user-service" in text
    assert "--system-service" in text
    assert "--allow-hub-url-override" in text


def test_setup_sh_does_not_always_install_system_unit() -> None:
    text = SETUP_SH.read_text(encoding="utf-8")
    assert "install_dashboard_systemd_unit" in text
    assert 'sudo tee "${SERVICE_FILE}"' not in text
