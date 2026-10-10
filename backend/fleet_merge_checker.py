"""Fleet-wide merge queue and branch protection settings drift checker.

Authority: Repository_Management#1890, Repository_Management#1900, Runner_Dashboard#1850.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

log = logging.getLogger("dashboard.fleet_merge_checker")

DEFAULT_POLICY_PATH = Path("config/fleet_merge_policy.json")


def load_policy(policy_path: Path) -> dict[str, Any]:
    """Load and return the fleet merge policy."""
    if not policy_path.is_file():
        raise FileNotFoundError(f"Policy file not found: {policy_path}")
    data = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Policy file must contain a JSON object: {policy_path}")
    return data


def _active_rulesets(rulesets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rulesets if r.get("enforcement", "active") == "active"]


def check_repo_settings(policy: dict[str, Any], repo_details: dict[str, Any]) -> list[str]:
    """Check repository flags against expected policy values."""
    problems: list[str] = []
    expected_flags = policy.get("repo_settings") or {}
    for flag, expected_val in sorted(expected_flags.items()):
        actual_val = repo_details.get(flag)
        if actual_val != expected_val:
            problems.append(f"repository flag {flag!r} is {actual_val!r}, expected {expected_val!r}")
    return problems


def check_merge_queue_ruleset(policy: dict[str, Any], rulesets: list[dict[str, Any]]) -> list[str]:
    """Verify an active ruleset defines merge_queue with the policy parameters."""
    wanted = policy.get("merge_queue") or {}
    if not wanted.get("required"):
        return []

    rules: list[dict[str, Any]] = []
    for ruleset in _active_rulesets(rulesets):
        for rule in ruleset.get("rules") or []:
            if rule.get("type") == "merge_queue":
                rules.append(rule)

    if not rules:
        return ["no active ruleset requires a merge queue on the default branch"]

    actual_params = rules[0].get("parameters") or {}
    expected_params = wanted.get("parameters") or {}
    problems: list[str] = []
    for key, expected_val in sorted(expected_params.items()):
        actual_val = actual_params.get(key)
        if actual_val != expected_val:
            problems.append(f"merge queue parameter {key!r} is {actual_val!r}, expected {expected_val!r}")
    return problems


def check_up_to_date_protection(
    policy: dict[str, Any],
    protection: dict[str, Any],
    rulesets: list[dict[str, Any]],
) -> list[str]:
    """Verify strict up-to-date checks are disabled when require_branches_up_to_date is false."""
    if policy.get("require_branches_up_to_date", True):
        return []

    problems: list[str] = []
    rsc = (protection or {}).get("required_status_checks") or {}
    if rsc.get("strict"):
        problems.append("classic branch protection requires branches to be up to date (strict=true)")

    for ruleset in _active_rulesets(rulesets):
        for rule in ruleset.get("rules") or []:
            params = rule.get("parameters") or {}
            if rule.get("type") == "required_status_checks" and params.get("strict_required_status_checks_policy"):
                problems.append(f"ruleset {ruleset.get('name')!r} requires branches to be up to date (strict=true)")
    return problems


def workflow_has_merge_group_trigger(workflow_text: str) -> bool:
    """Return True if the workflow yaml defines a merge_group trigger."""
    if yaml is None:  # pragma: no cover
        raise RuntimeError("PyYAML is required to parse workflow triggers")
    try:
        data = yaml.safe_load(workflow_text)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False

    triggers = data.get("on")
    if triggers is None:
        triggers = data.get(True)  # YAML parsing of unquoted 'on:' as boolean True
    if triggers is None:
        return False

    if isinstance(triggers, str):
        return triggers == "merge_group"
    if isinstance(triggers, list):
        return "merge_group" in triggers
    if isinstance(triggers, dict):
        return "merge_group" in triggers or True in triggers
    return False


def check_merge_group_triggers(
    workflows: dict[str, str],
    required_workflows: list[str],
) -> list[str]:
    """Verify that every required workflow defines a merge_group trigger."""
    problems: list[str] = []
    for wf_name in required_workflows:
        content = workflows.get(wf_name)
        if content is None:
            problems.append(f"required check workflow {wf_name!r} not found in repository")
            continue
        if not workflow_has_merge_group_trigger(content):
            problems.append(f"required check workflow {wf_name!r} is missing a 'merge_group:' trigger")
    return problems


def _normalize_workflow_name(name: str) -> str:
    return name.lower().replace("-", "").replace("_", "").replace(" ", "").replace(".yml", "").replace(".yaml", "")


def check_disallowed_workflows(
    policy: dict[str, Any],
    existing_workflows: list[str],
) -> list[str]:
    """Verify that disallowed workflows (e.g. Auto-Update PRs) are absent."""
    disallowed = (policy.get("workflows") or {}).get("disallowed_workflows") or []
    problems: list[str] = []
    for wf in disallowed:
        norm_wf = _normalize_workflow_name(wf)
        for existing in existing_workflows:
            if norm_wf in _normalize_workflow_name(existing):
                problems.append(f"disallowed workflow {wf!r} is present: {existing!r}")
    return problems


def check_single_repo(policy: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Evaluate drift for a single repository snapshot."""
    repo = snapshot.get("repo", "unknown")
    repo_details = snapshot.get("repo_details") or {}
    protection = snapshot.get("protection") or {}
    rulesets = snapshot.get("rulesets") or []
    workflows = snapshot.get("workflows") or {}
    required_workflows = snapshot.get("required_workflows") or []
    existing_workflow_names = snapshot.get("existing_workflow_names") or list(workflows.keys())

    findings: list[str] = []
    findings.extend(check_repo_settings(policy, repo_details))
    findings.extend(check_merge_queue_ruleset(policy, rulesets))
    findings.extend(check_up_to_date_protection(policy, protection, rulesets))
    findings.extend(check_merge_group_triggers(workflows, required_workflows))
    findings.extend(check_disallowed_workflows(policy, existing_workflow_names))

    return {
        "repo": repo,
        "status": "fail" if findings else "pass",
        "findings": findings,
    }


def check_fleet(
    policy: dict[str, Any],
    fleet_snapshots: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate drift across all repositories declared in the policy."""
    expected_repos = policy.get("repositories", [])
    results: list[dict[str, Any]] = []
    overall_status = "pass"

    for repo in expected_repos:
        snapshot = fleet_snapshots.get(repo)
        if snapshot is None:
            results.append(
                {
                    "repo": repo,
                    "status": "fail",
                    "findings": [f"repository snapshot missing for {repo!r}"],
                }
            )
            overall_status = "fail"
            continue

        res = check_single_repo(policy, snapshot)
        if res["status"] != "pass":
            overall_status = "fail"
        results.append(res)

    return {
        "status": overall_status,
        "results": results,
    }


def format_drift_issue(repo: str, findings: list[str]) -> tuple[str, str]:
    """Format an issue title and body for drift notifications."""
    title = f"fix(settings): merge-queue and branch-protection drift detected in {repo}"
    body_lines = [
        f"Automated drift detection identified configuration drift in `{repo}` "
        "relative to `config/fleet_merge_policy.json` (RM#1900 / RD#1850):",
        "",
    ]
    for finding in findings:
        body_lines.append(f"- [ ] {finding}")
    body_lines.extend(
        [
            "",
            "Please align repository settings, branch rulesets, or workflow triggers as "
            "described in D-sorganization/Repository_Management#1900.",
        ]
    )
    return title, "\n".join(body_lines)


def _github_api(url: str, *, token: str | None) -> Any:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if not url.startswith("https://api.github.com/"):
        raise ValueError(f"refusing non-GitHub-API URL: {url!r}")
    request = urllib.request.Request(url, headers=headers)
    # Scheme and host are pinned above, so file:/custom schemes cannot reach urlopen.
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def fetch_live_repo_snapshot(
    repo: str,
    token: str | None,
    default_branch: str | None = None,
) -> dict[str, Any]:
    """Fetch live settings for a single repository via GitHub REST API.

    Protection is read on ``default_branch`` when given, otherwise on the
    repository's own default branch (several fleet repos do not use ``main``).
    """
    api_root = f"https://api.github.com/repos/{repo}"
    repo_details = _github_api(api_root, token=token)
    default_branch = default_branch or repo_details.get("default_branch") or "main"

    try:
        protection = _github_api(f"{api_root}/branches/{default_branch}/protection", token=token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            protection = {}
        else:
            raise

    try:
        ruleset_summaries = _github_api(f"{api_root}/rulesets", token=token)
        rulesets = [
            _github_api(f"{api_root}/rulesets/{item['id']}", token=token)
            for item in ruleset_summaries
            if item.get("enforcement") == "active"
        ]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            rulesets = []
        else:
            raise

    try:
        workflows_resp = _github_api(f"{api_root}/actions/workflows", token=token)
        # Disabled and deleted workflows stay listed but cannot run.
        existing_workflow_names = [
            wf.get("name", "") for wf in workflows_resp.get("workflows", []) if wf.get("state", "active") == "active"
        ]
    except urllib.error.HTTPError:
        existing_workflow_names = []

    return {
        "repo": repo,
        "repo_details": repo_details,
        "protection": protection,
        "rulesets": rulesets,
        "workflows": {},
        "required_workflows": [],
        "existing_workflow_names": existing_workflow_names,
    }
