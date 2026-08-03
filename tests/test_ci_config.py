"""tests/test_ci_config.py — CI configuration contract tests (issue #400).

Asserts that ci-standard.yml, bandit.yaml, requirements-audit-ignore.txt, and
pyproject.toml satisfy the non-blocking/blocking policy introduced in #400:

  1. bandit step is blocking for HIGH (no continue-on-error, references bandit.yaml).
  2. pip-audit step reads requirements-audit-ignore.txt for MEDIUM/LOW waivers.
  3. pyproject.toml has disallow_untyped_defs = true globally.
  4. pyproject.toml constrains strict_optional = false to an explicit per-module
     override list only (not as a global default).
  5. The mypy Type Check step prints the override count to the CI log.
  6. bandit.yaml exists and contains a [skips] section with per-entry rationale.
  7. requirements-audit-ignore.txt exists and documents the policy.
  8. Lightweight CI jobs use the reversible hosted/local selector.
  9. Docker image builds remain on Docker-capable self-hosted runners.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci-standard.yml"
DOCKER_WORKFLOW = ROOT / ".github" / "workflows" / "docker-build.yml"
LOCAL_ONLY_GUARD = ROOT / ".github" / "workflows" / "local-only-runner-guard.yml"
PYPROJECT = ROOT / "pyproject.toml"
BANDIT_CONFIG = ROOT / "bandit.yaml"
AUDIT_IGNORE = ROOT / "requirements-audit-ignore.txt"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _workflow_text() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8")


def _workflow_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _runs_on_labels(job: dict) -> set[str]:
    runs_on = job["runs-on"]
    if isinstance(runs_on, str):
        return {runs_on}
    return {str(label) for label in runs_on}


def _local_only_guard_text() -> str:
    return LOCAL_ONLY_GUARD.read_text(encoding="utf-8")


def _pyproject_data() -> dict:  # type: ignore[type-arg]
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# CI workflow — bandit step
# ---------------------------------------------------------------------------


def test_bandit_step_not_continue_on_error() -> None:
    """bandit must not have continue-on-error: true (HIGH findings are blocking)."""
    text = _workflow_text()
    # Find the bandit step block
    bandit_idx = text.find("Run bandit security scan")
    assert bandit_idx != -1, "bandit step not found in ci-standard.yml"
    # Grab a window around the step — up to the next step marker
    step_window = text[bandit_idx : bandit_idx + 800]
    # continue-on-error: true must NOT appear in this step
    assert "continue-on-error: true" not in step_window, (
        "bandit step must NOT have continue-on-error: true — HIGH findings must block CI"
    )


def test_bandit_step_references_config() -> None:
    """bandit step must pass -c bandit.yaml so the allow-list config is used."""
    text = _workflow_text()
    bandit_idx = text.find("Run bandit security scan")
    assert bandit_idx != -1
    step_window = text[bandit_idx : bandit_idx + 800]
    assert "-c bandit.yaml" in step_window or "-c bandit.yaml" in text, (
        "bandit step must reference bandit.yaml via -c flag"
    )


# ---------------------------------------------------------------------------
# CI workflow — pip-audit step
# ---------------------------------------------------------------------------


def test_pip_audit_reads_ignore_file() -> None:
    """pip-audit step must reference requirements-audit-ignore.txt."""
    text = _workflow_text()
    audit_idx = text.find("Security Audit (pip-audit)")
    assert audit_idx != -1, "pip-audit step not found in ci-standard.yml"
    step_window = text[audit_idx : audit_idx + 1200]
    assert "requirements-audit-ignore.txt" in step_window, (
        "pip-audit step must read requirements-audit-ignore.txt for MEDIUM/LOW waivers"
    )


def test_security_scan_uses_isolated_no_cache_pip_audit() -> None:
    """security-scan must not execute pip-audit from the cache-backed project venv."""
    text = _workflow_text()
    scan_idx = text.find("security-scan:")
    tests_idx = text.find("\n  tests:", scan_idx)
    assert scan_idx != -1, "security-scan job not found in ci-standard.yml"
    assert tests_idx != -1, "tests job not found after security-scan in ci-standard.yml"
    scan_job = text[scan_idx:tests_idx]

    assert 'PIP_NO_CACHE_DIR: "1"' in scan_job
    assert "--no-cache-dir pip-audit" in scan_job
    assert "-r requirements.txt" in scan_job
    assert "./.venv/bin/python -m pip_audit" not in scan_job


# ---------------------------------------------------------------------------
# CI workflow — mypy override count notice
# ---------------------------------------------------------------------------


def test_mypy_step_prints_override_count() -> None:
    """mypy Type Check step must emit the override count as a CI notice."""
    text = _workflow_text()
    assert "mypy relaxed-override module count" in text, (
        "mypy step must print override count via ::notice:: so it cannot grow silently"
    )


# ---------------------------------------------------------------------------
# CI workflow — runner labels
# ---------------------------------------------------------------------------


def test_ci_uses_reversible_zero_polling_runner_selector() -> None:
    """Public CI can use hosted capacity without adding GitHub API polling."""
    text = _workflow_text()
    workflow = _workflow_yaml(CI_WORKFLOW)
    picker = workflow["jobs"]["pick-runner"]
    assert "CI_RUNNER_MODE" in text
    assert "ubuntu-latest" in text
    assert "d-sorg-fleet" in text
    assert "gh api" not in str(picker)
    assert "gh repo list" not in str(picker)
    for job_name in ("ci-health-check", "quality-gate", "security-scan", "tests", "tests-required"):
        assert "needs.pick-runner.outputs.runner" in str(workflow["jobs"][job_name]["runs-on"])


def test_test_matrix_fanout_is_bounded() -> None:
    workflow = _workflow_yaml(CI_WORKFLOW)
    assert workflow["jobs"]["tests"]["strategy"]["max-parallel"] <= 3


def test_docker_build_uses_docker_runners() -> None:
    """Docker builds require Docker-capable runner labels."""
    workflow = _workflow_yaml(DOCKER_WORKFLOW)
    labels = _runs_on_labels(workflow["jobs"]["docker-build-scan"])
    assert {"self-hosted", "Linux", "X64", "d-sorg-fleet-docker"}.issubset(labels)
    text = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    assert "cache-from: type=gha" in text
    assert "cache-to: type=gha,mode=max" in text


def test_local_only_guard_runs_on_fleet() -> None:
    """The hosted-runner guard must not depend on hosted runners itself."""
    text = _local_only_guard_text()
    assert "runs-on: d-sorg-fleet" in text
    assert "runs-on: ubuntu-latest" not in text


def test_main_line_cap_exempts_current_legacy_frontend_baseline() -> None:
    """Main push line-cap gate must not fail on documented legacy frontend debt."""
    text = _workflow_text()
    legacy_files = {
        "Principals.tsx",
        "Analysis.tsx",
        "RemediationPRs.tsx",
        "Machines.tsx",
        "FleetTab.tsx",
        "FleetOrchestration.tsx",
        "FeatureRequests.tsx",
        "RemediationTab.tsx",
        "Workflows.tsx",
        "RemediationIssues.tsx",
        "navRegistry.ts",
    }
    for filename in legacy_files:
        assert filename in text, f"{filename} missing from line-cap legacy baseline"


# ---------------------------------------------------------------------------
# pyproject.toml — mypy global defaults
# ---------------------------------------------------------------------------


def test_mypy_disallow_untyped_defs_global_true() -> None:
    """Global disallow_untyped_defs must be true so new modules are strictly typed."""
    data = _pyproject_data()
    mypy_cfg = data.get("tool", {}).get("mypy", {})
    assert mypy_cfg.get("disallow_untyped_defs") is True, (
        "pyproject.toml [tool.mypy] disallow_untyped_defs must be true globally"
    )


def test_mypy_strict_optional_not_disabled_globally() -> None:
    """strict_optional must NOT be false at the global level."""
    data = _pyproject_data()
    mypy_cfg = data.get("tool", {}).get("mypy", {})
    # Either absent (defaults to true) or explicitly true is acceptable.
    assert mypy_cfg.get("strict_optional", True) is not False, (
        "pyproject.toml [tool.mypy] strict_optional must not be globally false; "
        "restrict it to per-module overrides only"
    )


def test_mypy_overrides_strict_optional_are_per_module() -> None:
    """strict_optional=false must only appear in per-module [[tool.mypy.overrides]] sections."""
    data = _pyproject_data()
    overrides = data.get("tool", {}).get("mypy", {}).get("overrides", [])
    relaxed_modules = [o.get("module") for o in overrides if o.get("strict_optional") is False]
    # There may be relaxed modules (legacy godfiles), but each must name specific modules.
    for entry in relaxed_modules:
        modules = entry if isinstance(entry, list) else [entry]
        assert all(isinstance(m, str) and m for m in modules), (
            f"Every strict_optional=false override must name specific modules; got: {entry}"
        )


def test_mypy_override_list_does_not_grow() -> None:
    """The relaxed-override list must not exceed the current issue #400 baseline."""
    data = _pyproject_data()
    overrides = data.get("tool", {}).get("mypy", {}).get("overrides", [])
    relaxed_count = 0
    for o in overrides:
        if o.get("disallow_untyped_defs") is False or o.get("strict_optional") is False:
            modules = o.get("module", [])
            if isinstance(modules, list):
                relaxed_count += len(modules)
            else:
                relaxed_count += 1
    # Baseline: 24 legacy modules present when the CI guard was restored.
    assert relaxed_count <= 24, (
        f"mypy relaxed-override list has grown to {relaxed_count} modules "
        f"(baseline: 24). Remove modules from the override list in #161."
    )


# ---------------------------------------------------------------------------
# bandit.yaml
# ---------------------------------------------------------------------------


def test_bandit_yaml_exists() -> None:
    """bandit.yaml must exist at the repo root."""
    assert BANDIT_CONFIG.exists(), (
        "bandit.yaml must exist at repo root — it defines the MEDIUM allow-list with rationale"
    )


def test_bandit_yaml_has_skips_section() -> None:
    """bandit.yaml must contain a skips: section."""
    text = BANDIT_CONFIG.read_text(encoding="utf-8")
    assert "skips:" in text, "bandit.yaml must contain a skips: section"


def test_bandit_yaml_skips_have_rationale_comments() -> None:
    """Each skip entry in bandit.yaml must be preceded by a rationale comment."""
    text = BANDIT_CONFIG.read_text(encoding="utf-8")
    # Every line that starts with '  - B' (a skip entry) must have a comment block above it.
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- B") and i > 0:
            # Check the preceding non-blank lines for a comment
            preceding = "\n".join(lines[max(0, i - 10) : i])
            assert "#" in preceding, f"Skip entry '{stripped}' at line {i + 1} in bandit.yaml has no rationale comment"


# ---------------------------------------------------------------------------
# requirements-audit-ignore.txt
# ---------------------------------------------------------------------------


def test_requirements_audit_ignore_exists() -> None:
    """requirements-audit-ignore.txt must exist at the repo root."""
    assert AUDIT_IGNORE.exists(), (
        "requirements-audit-ignore.txt must exist at repo root — it defines the pip-audit MEDIUM/LOW CVE allow-list"
    )


def test_requirements_audit_ignore_has_policy_header() -> None:
    """requirements-audit-ignore.txt must document the CRITICAL/HIGH blocking policy."""
    text = AUDIT_IGNORE.read_text(encoding="utf-8")
    assert "CRITICAL" in text or "HIGH" in text, (
        "requirements-audit-ignore.txt must document that CRITICAL/HIGH CVEs are blocking"
    )
