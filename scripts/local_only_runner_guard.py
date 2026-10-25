"""Job-level guard: only allowlisted required-check jobs may use hosted fast-check routing."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from scripts.ci_runner_selector import (
    RD_FAST_CHECKS_VAR,
    hosted_required_check_jobs,
    runs_on_uses_fast_checks_lane,
)

HOSTED_LITERAL = re.compile(r"^(ubuntu|macos|windows)(-latest|-\d+)?$")

# Workflows that intentionally use the reversible public selector on every job.
_HYBRID_WORKFLOW_FILES = frozenset(
    {
        "anti-phantom-merge.yml",
        "ci-secrets.yml",
        "ci-spec-check.yml",
        "ci-standard.yml",
        "docker-build.yml",
        "frontend-tests.yml",
        "lint-workflow-files.yml",
        "Agent-Redundant-PR-Closer.yml",
    }
)

# Self-check and canary workflows (not subject to fleet-only enforcement).
_EXEMPT_WORKFLOW_FILES = frozenset(
    {
        "local-only-runner-guard.yml",
        "runner-health-alert.yml",
    }
)


def collect_runs_on(job: dict) -> list[str]:
    runs_on = job.get("runs-on")
    values: list[str] = []
    if isinstance(runs_on, str):
        values.append(runs_on)
    elif isinstance(runs_on, list):
        values.extend(v for v in runs_on if isinstance(v, str))
    if any("matrix.os" in v for v in values):
        values = []
        strategy = job.get("strategy") or {}
        matrix = strategy.get("matrix") or {}
        if isinstance(matrix, dict):
            os_axis = matrix.get("os")
            if isinstance(os_axis, list):
                values.extend(v for v in os_axis if isinstance(v, str))
            include = matrix.get("include")
            if isinstance(include, list):
                for item in include:
                    if isinstance(item, dict) and isinstance(item.get("os"), str):
                        values.append(item["os"])
    return values


def audit_workflows(workflow_dir: Path) -> list[str]:
    repo_root = workflow_dir.parent.parent
    allowlisted = hosted_required_check_jobs(repo_root)
    violations: list[str] = []

    if not workflow_dir.is_dir():
        return violations

    for wf_path in sorted(workflow_dir.glob("*.y*ml")):
        if wf_path.name in _EXEMPT_WORKFLOW_FILES:
            continue
        try:
            data = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            violations.append(f"{wf_path.name}: failed to parse YAML: {exc}")
            continue
        if not isinstance(data, dict):
            continue
        jobs = data.get("jobs") or {}
        if not isinstance(jobs, dict):
            continue

        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            if "uses" in job and "runs-on" not in job:
                continue

            raw = job.get("runs-on")
            raw_text = raw if isinstance(raw, str) else ""
            uses_fast = runs_on_uses_fast_checks_lane(raw_text)
            is_allowlisted = (wf_path.name, job_id) in allowlisted

            if uses_fast and not is_allowlisted:
                violations.append(
                    f"{wf_path.name}::{job_id}: uses {RD_FAST_CHECKS_VAR} or fast_checks_runner "
                    "but is not an allowlisted required-check job"
                )
            if is_allowlisted and not uses_fast:
                violations.append(
                    f"{wf_path.name}::{job_id}: required-check job must use fast-check runner routing"
                )

            if wf_path.name in _HYBRID_WORKFLOW_FILES:
                continue

            values = collect_runs_on(job)
            if not values and isinstance(raw, str):
                values = [raw]
            for value in values:
                v_clean = value.strip()
                if (
                    "pick-runner" in v_clean
                    or "d-sorg-fleet" in v_clean
                    or "self-hosted" in v_clean
                    or RD_FAST_CHECKS_VAR in v_clean
                ):
                    continue
                if HOSTED_LITERAL.match(v_clean):
                    violations.append(
                        f"{wf_path.name}::{job_id}: runs-on '{v_clean}' is a hosted runner; "
                        "route to d-sorg-fleet or the pick-runner output."
                    )

    return violations


def main() -> int:
    violations = audit_workflows(Path(".github/workflows"))
    if violations:
        for item in violations:
            print(f"::error::{item}")
        print(f"{len(violations)} workflow job(s) leak to hosted runners.")
        return 1
    print("All workflows route to d-sorg-fleet except allowlisted required-check jobs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
