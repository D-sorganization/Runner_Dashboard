"""Detect drift between the intended required-status-checks policy and the
live branch-protection / ruleset configuration for the default branch.

Background (issue #1119): PR #1116 merged before its late full-suite failure
surfaced, and docs-only PR #1118 merged while the Anti-Phantom Merge Guard
was still queued. Neither event was a code bug in the PR itself -- both were
caused by the *set of required status checks* on `main` not matching the set
of checks the repository actually intends to gate merges on.

This script has two independent checks:

1. ``required_context_drift`` -- compares
   ``config/required_status_checks_policy.json`` (the intended list) against
   the union of contexts required by classic branch protection and any
   active branch rulesets. Contexts present in the policy but missing from
   the live configuration are reported as drift.

2. ``check_job_fails_closed`` -- a static check on a GitHub Actions workflow
   file: for a job that is meant to be a required check (e.g. the `tests`
   aggregate job in ci-standard.yml), verifies the job uses `if: always()`
   (or another unconditional guard). Without it, GitHub Actions skips the
   job whenever any of its `needs` fail, and a SKIPPED conclusion on a
   required check does NOT block merge -- this is the exact mechanism that
   let PR #1116 merge despite a failing pytest matrix leg.

Both checks can run entirely offline against committed fixtures (see
tests/test_required_checks_drift.py and tests/contracts/*.json), or against
live data via --live (requires GH_TOKEN with permission to read branch
protection / rulesets, which the default GITHUB_TOKEN typically lacks).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a repo dependency
    yaml = None  # type: ignore[assignment]

DEFAULT_POLICY = "config/required_status_checks_policy.json"
DEFAULT_REPO = "D-sorganization/Runner_Dashboard"
DEFAULT_BRANCH = "main"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _github_api(url: str, *, token: str | None) -> Any:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def fetch_live_snapshot(repo: str, branch: str, token: str | None) -> tuple[Any, list[Any]]:
    """Fetch classic branch protection + all active rulesets for ``branch``."""
    api_root = f"https://api.github.com/repos/{repo}"
    protection = _github_api(f"{api_root}/branches/{branch}/protection", token=token)
    ruleset_summaries = _github_api(f"{api_root}/rulesets", token=token)
    rulesets = [
        _github_api(f"{api_root}/rulesets/{item['id']}", token=token)
        for item in ruleset_summaries
        if item.get("enforcement") == "active"
    ]
    return protection, rulesets


def required_contexts_from_snapshot(protection: Any, rulesets: list[Any]) -> set[str]:
    """Union of required-status-check contexts from classic protection and
    active rulesets. Mirrors GitHub's actual merge-gating behavior: a PR
    must satisfy every applicable protection source, so any context
    required by ANY source is effectively required overall.
    """
    contexts: set[str] = set()

    rsc = (protection or {}).get("required_status_checks") or {}
    for context in rsc.get("contexts") or []:
        contexts.add(context)
    for check in rsc.get("checks") or []:
        if check.get("context"):
            contexts.add(check["context"])

    for ruleset in rulesets:
        for rule in ruleset.get("rules") or []:
            if rule.get("type") != "required_status_checks":
                continue
            for check in (rule.get("parameters") or {}).get("required_status_checks") or []:
                if check.get("context"):
                    contexts.add(check["context"])

    return contexts


def required_context_drift(policy: Any, protection: Any, rulesets: list[Any]) -> list[str]:
    """Return the list of policy contexts that are NOT currently required
    anywhere in the live branch-protection/ruleset configuration.
    """
    live_contexts = required_contexts_from_snapshot(protection, rulesets)
    policy_contexts = [entry["context"] for entry in policy.get("required_contexts", [])]
    return [context for context in policy_contexts if context not in live_contexts]


def check_job_fails_closed(workflow_text: str, job_id: str) -> list[str]:
    """Static check: does the job that reports a required check context fail
    closed (i.e. does its own conclusion reflect its dependencies' failure
    instead of being silently SKIPPED)?

    Returns a list of human-readable problems; empty means the job looks
    safe. This does not modify or lint the workflow beyond this narrow
    check -- workflow YAML changes are a governed action (see issue #1119
    PR description) and are left to a human/maintainer to apply.
    """
    if yaml is None:  # pragma: no cover
        raise RuntimeError("PyYAML is required to parse workflow files")

    problems: list[str] = []
    document = yaml.safe_load(workflow_text)
    jobs = document.get("jobs", {})
    job = jobs.get(job_id)
    if job is None:
        problems.append(f"job '{job_id}' not found in workflow")
        return problems

    needs = job.get("needs")
    if not needs:
        # Nothing to fail closed against.
        return problems

    condition = str(job.get("if", ""))
    if "always()" not in condition and "!cancelled()" not in condition:
        problems.append(
            f"job '{job_id}' depends on {needs!r} but has no `if: always()` "
            "(or `!cancelled()`) guard, so it is SKIPPED -- not FAILED -- "
            "whenever a dependency fails. GitHub treats a SKIPPED conclusion "
            "on a required check as non-blocking, so this job can silently "
            "let a failing dependency through (see PR #1116)."
        )

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--branch-protection-snapshot")
    parser.add_argument("--rulesets-snapshot")
    parser.add_argument("--live", action="store_true", help="fetch live data from the GitHub API")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument(
        "--workflow",
        default=".github/workflows/ci-standard.yml",
        help="workflow file to run the fails-closed check against",
    )
    parser.add_argument(
        "--required-job-id",
        default="tests-required",
        help="job id (not display name) that must fail closed",
    )
    parser.add_argument("--skip-fails-closed-check", action="store_true")
    args = parser.parse_args(argv)

    policy = _load_json(Path(args.policy))

    if args.live:
        token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
        protection, rulesets = fetch_live_snapshot(args.repo, args.branch, token)
    else:
        if not args.branch_protection_snapshot or not args.rulesets_snapshot:
            parser.error("--branch-protection-snapshot and --rulesets-snapshot are required unless --live is set")
        protection = _load_json(Path(args.branch_protection_snapshot))
        rulesets_data = _load_json(Path(args.rulesets_snapshot))
        rulesets = rulesets_data if isinstance(rulesets_data, list) else [rulesets_data]

    problems: list[str] = []

    missing = required_context_drift(policy, protection, rulesets)
    if missing:
        problems.append(
            "Required-status-checks drift: the following contexts are declared "
            f"required in {args.policy} but are NOT required by the live branch "
            f"protection/ruleset configuration: {sorted(missing)}"
        )

    if not args.skip_fails_closed_check:
        workflow_path = Path(args.workflow)
        if workflow_path.exists():
            fails_closed_problems = check_job_fails_closed(
                workflow_path.read_text(encoding="utf-8"), args.required_job_id
            )
            problems.extend(fails_closed_problems)

    if problems:
        for problem in problems:
            print(f"::error::{problem}", file=sys.stderr)
        return 1

    print("Required-status-checks configuration matches policy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
