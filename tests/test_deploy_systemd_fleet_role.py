"""Deploy contract: fleet role must not be hardcoded in the systemd template (#2022)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "deploy" / "runner-dashboard.service"
SETUP_SH = REPO_ROOT / "deploy" / "setup.sh"


def test_runner_dashboard_unit_template_has_no_machine_role() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "Environment=MACHINE_ROLE=" not in text, (
        "runner-dashboard.service must not set MACHINE_ROLE; role comes from "
        "machine_registry.yml unless an operator passes --role to setup.sh"
    )


def test_setup_sh_strips_stale_machine_role_and_supports_override() -> None:
    setup = SETUP_SH.read_text(encoding="utf-8")
    assert "ROLE_ENV_OVERRIDE" in setup
    assert "Environment=MACHINE_ROLE=" in setup
    assert "MACHINE_ROLE=hub|MACHINE_ROLE=${MACHINE_ROLE}" not in setup
    assert "registry_resolved_role" in setup
