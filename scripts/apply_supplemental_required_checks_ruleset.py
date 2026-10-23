"""Create or update the repository ruleset that supplements org branch protections.

Preconditions (Design by Contract):
  - Supplement JSON exists and declares at least one required context.
  - GH_TOKEN (or GITHUB_TOKEN) is non-empty and has permission to manage rulesets.
  - Every supplement context appears in config/required_status_checks_policy.json.

The supplement is applied with workflow_dispatch (BRANCH_PROTECTION_TOKEN) after the
policy and workflow changes merge, so GitHub never waits for checks that do not exist.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_REPO = "D-sorganization/Runner_Dashboard"
DEFAULT_POLICY = "config/required_status_checks_policy.json"
DEFAULT_SUPPLEMENT = "config/repository_rulesets/required-status-checks-supplement.json"


class PreconditionError(ValueError):
    """A caller-visible contract violation."""


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def require_admin_token(token: str | None) -> str:
    if not token or not str(token).strip():
        raise PreconditionError("GH_TOKEN or GITHUB_TOKEN is required to manage rulesets")
    return str(token).strip()


def require_supplement_path(path: Path) -> Path:
    if not path.is_file():
        raise PreconditionError(f"supplement document not found: {path}")
    return path


def required_contexts_from_supplement(document: dict[str, Any]) -> list[str]:
    contexts: list[str] = []
    for rule in document.get("rules") or []:
        if rule.get("type") != "required_status_checks":
            continue
        for check in (rule.get("parameters") or {}).get("required_status_checks") or []:
            context = check.get("context")
            if context:
                contexts.append(context)
    return contexts


def validate_supplement_document(document: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if not document.get("ruleset_name"):
        problems.append("ruleset_name is required")
    checks = required_contexts_from_supplement(document)
    if not checks:
        problems.append("required_status_checks must list at least one context")
    if document.get("target") != "branch":
        problems.append("supplement ruleset must target branch")
    return problems


def validate_supplement_against_policy(policy: dict[str, Any], document: dict[str, Any]) -> list[str]:
    policy_contexts = {entry["context"] for entry in policy.get("required_contexts") or []}
    problems = validate_supplement_document(document)
    for context in required_contexts_from_supplement(document):
        if context not in policy_contexts:
            problems.append(f"context {context!r} is not declared in required_status_checks_policy.json")
    return problems


def build_github_ruleset_payload(document: dict[str, Any]) -> dict[str, Any]:
    problems = validate_supplement_document(document)
    if problems:
        raise PreconditionError("; ".join(problems))
    return {
        "name": document["ruleset_name"],
        "target": document["target"],
        "enforcement": document.get("enforcement", "active"),
        "conditions": document.get("conditions") or {},
        "rules": document.get("rules") or [],
    }


@dataclass(frozen=True)
class UpsertPlan:
    action: str
    ruleset_id: int | None
    payload: dict[str, Any]


def plan_upsert(document: dict[str, Any], existing_rulesets: list[dict[str, Any]]) -> UpsertPlan:
    payload = build_github_ruleset_payload(document)
    name = document["ruleset_name"]
    for item in existing_rulesets:
        if item.get("name") == name:
            return UpsertPlan(action="update", ruleset_id=int(item["id"]), payload=payload)
    return UpsertPlan(action="create", ruleset_id=None, payload=payload)


def _github_api(
    method: str,
    url: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
) -> Any:
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {token}",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} failed ({exc.code}): {detail}") from exc


def list_ruleset_summaries(repo: str, token: str) -> list[dict[str, Any]]:
    return _github_api("GET", f"https://api.github.com/repos/{repo}/rulesets", token=token)


def apply_plan(repo: str, token: str, plan: UpsertPlan, *, dry_run: bool) -> dict[str, Any]:
    api_root = f"https://api.github.com/repos/{repo}/rulesets"
    if dry_run:
        return {"dry_run": True, "action": plan.action, "ruleset_id": plan.ruleset_id, "payload": plan.payload}
    if plan.action == "create":
        return _github_api("POST", api_root, token=token, body=plan.payload)
    assert plan.ruleset_id is not None
    return _github_api("PUT", f"{api_root}/{plan.ruleset_id}", token=token, body=plan.payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--supplement", default=DEFAULT_SUPPLEMENT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    supplement_path = require_supplement_path(Path(args.supplement))
    document = _load_json(supplement_path)
    policy = _load_json(Path(args.policy))

    problems = validate_supplement_against_policy(policy, document)
    if problems:
        for problem in problems:
            print(f"::error::{problem}", file=sys.stderr)
        return 1

    if args.dry_run:
        summaries: list[dict[str, Any]] = []
    else:
        token = require_admin_token(os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN"))
        summaries = list_ruleset_summaries(args.repo, token)
    plan = plan_upsert(document, summaries)
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
    result = apply_plan(args.repo, token, plan, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
