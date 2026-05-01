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
  8. All jobs in ci-standard.yml run on d-sorg-fleet (not ubuntu-latest).
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # Python < 3.11

ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci-standard.yml"
PYPROJECT = ROOT / "pyproject.toml"
BANDIT_CONFIG = ROOT / "bandit.yaml"
AUDIT_IGNORE = ROOT / "requirements-audit-ignore.txt"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _workflow_text() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8")


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


def test_all_jobs_use_fleet_runner() -> None:
    """Every job must use runs-on: d-sorg-fleet, not ubuntu-latest."""
    text = _workflow_text()
    bad_runners = re.findall(r"runs-on:\s*(ubuntu-latest|ubuntu-\d+\.\d+)", text)
    assert not bad_runners, (
        f"Found non-fleet runner(s) in ci-standard.yml: {bad_runners}. All jobs must use runs-on: d-sorg-fleet"
    )


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
