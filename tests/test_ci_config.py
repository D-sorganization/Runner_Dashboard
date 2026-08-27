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

import ast
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


def test_job_level_environment_uses_pre_runner_contexts_only() -> None:
    """Job-level env is evaluated before runner context becomes available."""
    workflow = _workflow_yaml(CI_WORKFLOW)
    for job_name, job in workflow["jobs"].items():
        for value in (job.get("env") or {}).values():
            assert "runner.temp" not in str(value), (
                f"{job_name} uses runner.temp in job-level env; use github.workspace or step-level env"
            )


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
    assert "github/codeql-action/upload-sarif" not in str(workflow["jobs"]["docker-build-scan"])


def test_docker_sarif_publication_uses_reversible_lightweight_route() -> None:
    """Only SARIF publication may leave the local Docker runner."""
    workflow = _workflow_yaml(DOCKER_WORKFLOW)
    publisher = workflow["jobs"]["publish-sarif"]
    runs_on = str(publisher["runs-on"])
    assert "ubuntu-latest" in runs_on
    assert "CI_RUNNER_MODE" in runs_on
    assert "d-sorg-fleet" in runs_on
    assert "github/codeql-action/upload-sarif" in str(publisher)


def test_local_only_guard_uses_reversible_public_fast_lane() -> None:
    """The routing guard must remain available while local pools are drained."""
    text = _local_only_guard_text()
    assert "ubuntu-latest" in text
    assert "CI_RUNNER_MODE" in text
    assert "d-sorg-fleet" in text
    assert '"anti-phantom-merge.yml"' in text


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


# ---------------------------------------------------------------------------
# CI workflow — Python validation scope detector (issue #1093)
#
# `quality-gate` is the only required status check in the "Repository_Protections"
# ruleset, and it — along with security-scan, tests, and tests-required — is
# gated on `ci-health-check.outputs.run_python_tests`. Any path the detector
# fails to recognise therefore merges with zero test signal. PR #1092 merged a
# new regression test that never executed in CI for exactly this reason.
# ---------------------------------------------------------------------------

# Directory prefixes whose changes must trigger the Python lane, mapped to the
# noun used for them in the "Report skipped Python validation" message. The two
# must stay in sync in BOTH directions: a noun the message advertises has to be
# a prefix the detector actually tests, and vice versa.
SCOPE_PREFIX_NOUNS = {
    "backend/": "backend",
    "tests/": "tests",
    "deploy/": "deploy",
}

# Jobs whose `if:` gates on the detector. Losing any of these on a mis-detected
# PR is what makes the defect a merge-blocking-signal loss rather than a
# cosmetic one.
PYTHON_GATED_JOBS = ("quality-gate", "security-scan", "tests", "tests-required")


def _ci_health_steps() -> list[dict]:
    data = _workflow_yaml(CI_WORKFLOW)
    return data["jobs"]["ci-health-check"]["steps"]


def _step_by(key: str, value: str) -> dict:
    for step in _ci_health_steps():
        if step.get(key) == value:
            return step
    raise AssertionError(f"ci-health-check has no step with {key}={value!r}")


def _scope_detector_source() -> str:
    """Return the Python heredoc body from the `python-scope` step.

    Reading it through the YAML parser (rather than the raw file) means the
    block scalar is already dedented, so the result is importable source.
    """
    run = _step_by("id", "python-scope")["run"]
    lines = run.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if "<<'EOF'" in line),
        None,
    )
    assert start is not None, "python-scope step no longer uses an <<'EOF' heredoc"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].strip() == "EOF"),
        None,
    )
    assert end is not None, "python-scope heredoc is not terminated by EOF"
    return "\n".join(lines[start + 1 : end])


def _detector_literal(name: str) -> object:
    """Evaluate a module-level literal assignment from the detector source."""
    tree = ast.parse(_scope_detector_source())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"detector does not assign a literal named {name!r}")


def _detector_selects(files: list[str]) -> bool:
    """Mirror the detector's predicate using its own prefix/exact literals."""
    prefixes = _detector_literal("prefixes")
    exact = _detector_literal("exact")
    assert isinstance(prefixes, tuple), "prefixes must stay a tuple for str.startswith"
    return any(path.startswith(prefixes) or path in exact for path in files)


def test_scope_detector_predicate_shape_is_unchanged() -> None:
    """Guard the mirror in `_detector_selects` against a detector rewrite.

    If the workflow stops combining `startswith(prefixes)` with `in exact`, the
    behavioural tests below silently stop describing reality.
    """
    source = _scope_detector_source()
    assert "path.startswith(prefixes)" in source, (
        "detector no longer uses path.startswith(prefixes) — update _detector_selects"
    )
    assert "path in exact" in source, "detector no longer uses `path in exact` — update _detector_selects"


def test_scope_detector_covers_documented_prefixes() -> None:
    """Every directory the skip message advertises must actually be detected."""
    prefixes = _detector_literal("prefixes")
    for prefix in SCOPE_PREFIX_NOUNS:
        assert prefix in prefixes, (
            f"{prefix!r} missing from the python-scope detector — PRs touching only "
            f"{prefix} skip quality-gate, the sole required status check"
        )


def test_test_only_pr_runs_python_lane() -> None:
    """A test-only PR must run pytest (regression for PR #1092)."""
    assert _detector_selects(["tests/deploy/test_wsl_keepalive_script.py"]), (
        "a PR that only adds a regression test would skip pytest entirely"
    )


def test_deploy_only_pr_runs_python_lane() -> None:
    """Deploy scripts are covered by tests/deploy/, so they must run the lane."""
    assert _detector_selects(["deploy/wsl-keepalive.ps1"]), (
        "deploy/ changes skip the pytest suite that exercises them (tests/deploy/)"
    )


def test_docs_only_pr_still_skips_python_lane() -> None:
    """The detector must stay selective — this is the whole point of scoping."""
    assert not _detector_selects(["docs/architecture.md", "README.md"]), (
        "detector became unconditionally true; the scoping optimisation is gone"
    )


def test_skip_message_and_detector_agree() -> None:
    """The skip message must describe exactly what the detector checks.

    The original defect was a message advertising `tests` coverage that the
    detector never implemented.
    """
    message = _step_by("name", "Report skipped Python validation")["run"]
    prefixes = _detector_literal("prefixes")

    for prefix, noun in SCOPE_PREFIX_NOUNS.items():
        if prefix in prefixes:
            assert noun in message, (
                f"detector covers {prefix!r} but the skip message never mentions "
                f"{noun!r} — operators cannot tell what was skipped"
            )

    for noun, prefix in ((n, p) for p, n in SCOPE_PREFIX_NOUNS.items()):
        if noun in message:
            assert prefix in prefixes, (
                f"skip message claims {noun!r} is covered but the detector does not test the {prefix!r} prefix"
            )


def test_python_gated_jobs_are_known() -> None:
    """Pin the blast radius of the detector so new gated jobs are deliberate."""
    data = _workflow_yaml(CI_WORKFLOW)
    gated = {name for name, job in data["jobs"].items() if "run_python_tests" in str(job.get("if", ""))}
    assert gated == set(PYTHON_GATED_JOBS), (
        f"set of detector-gated jobs changed: {sorted(gated)}. Every job added "
        "here inherits the scope detector's blind spots — confirm that is intended"
    )
