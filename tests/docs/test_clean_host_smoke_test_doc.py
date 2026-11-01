"""Keep docs/clean-host-smoke-test.md honest about the real install/upgrade/rollback scripts (#3726)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "clean-host-smoke-test.md"
SETUP_SCRIPT = ROOT / "deploy" / "setup.sh"
UPDATE_SCRIPT = ROOT / "deploy" / "update-deployed.sh"
ROLLBACK_SCRIPT = ROOT / "deploy" / "rollback.sh"
DEPLOY_CHECK_SCRIPT = ROOT / "deploy" / "deploy-check.sh"


def test_doc_exists() -> None:
    assert DOC.is_file(), "docs/clean-host-smoke-test.md must exist (#3726)"


def test_doc_names_the_real_scripts_for_each_phase() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "deploy/setup.sh" in text
    assert "deploy/update-deployed.sh" in text
    assert "deploy/rollback.sh" in text
    assert "deploy/deploy-check.sh" in text
    for script in (SETUP_SCRIPT, UPDATE_SCRIPT, ROLLBACK_SCRIPT, DEPLOY_CHECK_SCRIPT):
        assert script.is_file(), f"{script} referenced by the smoke test doc no longer exists"


def test_doc_is_a_manual_runbook_not_a_ci_job() -> None:
    """HOLD #2657 blocks any RD deploy or release automation; the smoke test
    procedure must stay a human-run checklist, not a scheduled/triggered
    workflow, until that hold lifts."""
    text = DOC.read_text(encoding="utf-8")
    assert "HOLD #2657" in text or "hold #2657" in text.lower()
    assert "manual" in text.lower()
    ci_smoke_names = [
        p.name
        for p in (ROOT / ".github" / "workflows").glob("*.yml")
        if "smoke" in p.name.lower()
    ]
    for name in ci_smoke_names:
        workflow_text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "install" not in workflow_text.lower() or "deploy/setup.sh" not in workflow_text, (
            f".github/workflows/{name} now automates a real install/upgrade/rollback cycle; "
            "docs/clean-host-smoke-test.md's 'no automated clean-host smoke test exists yet' "
            "claim is stale"
        )


def test_doc_covers_install_upgrade_and_rollback_in_order() -> None:
    text = DOC.read_text(encoding="utf-8").lower()
    install_idx = text.find("## install")
    upgrade_idx = text.find("## upgrade")
    rollback_idx = text.find("## rollback")
    assert -1 not in (install_idx, upgrade_idx, rollback_idx), (
        "doc must have Install, Upgrade, and Rollback sections"
    )
    assert install_idx < upgrade_idx < rollback_idx


def test_doc_cross_linked_from_deployment_model_and_install_paths() -> None:
    deployment_model = (ROOT / "docs" / "deployment-model.md").read_text(encoding="utf-8")
    install_paths = (ROOT / "docs" / "install-paths.md").read_text(encoding="utf-8")
    assert "clean-host-smoke-test.md" in deployment_model
    assert "clean-host-smoke-test.md" in install_paths
