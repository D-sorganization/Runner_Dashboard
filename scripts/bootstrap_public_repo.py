"""Plan repo settings-as-code for the fresh public Runner_Dashboard repo.

(#3083, public-repo slice 5, epic #3077.) Dry-run by default: computes a
settings diff against a (mockable) GitHub API and changes nothing. Apply is
an owner-run step gated by three DbC preconditions -- zero open PRs on the
source repo, a clean git guard, and an explicit admin-session confirmation
per AGENTS.md's GOV-1 rule -- and never issues a mutating GitHub call itself,
because the target repo this plans for does not exist yet; rename, archive,
repo creation and the visibility flip are owner steps.

Required status checks and merge-queue parameters are read from
config/required_status_checks_policy.json instead of duplicated here (DRY).
The export check reuses slice 2's publish_guard (the shared publish-allowlist
pattern list) and slice 3's secret scan.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = ROOT / "config" / "public_repo_settings.json"
DEFAULT_CHECKS_POLICY = ROOT / "config" / "required_status_checks_policy.json"
DEFAULT_HOLDS_POLICY = ROOT / "config" / "rd_barb_locked_policy.json"

GitHubGet = Callable[..., Any]


class PreconditionError(ValueError):
    """A caller-visible Design-by-Contract violation."""


def require_zero_open_prs(open_pr_count: int) -> None:
    if open_pr_count != 0:
        raise PreconditionError(f"source repo has {open_pr_count} open PR(s); apply refuses with open PRs")


def require_clean_guard(is_dirty: bool) -> None:
    if is_dirty:
        raise PreconditionError("source repo working tree is dirty; apply refuses with a dirty guard")


def require_admin_session(confirmed: bool) -> None:
    if not confirmed:
        raise PreconditionError(
            "apply requires an explicit admin-session confirmation (AGENTS.md GOV-1); set RD_ADMIN_SESSION_CONFIRMED=1"
        )


def apply_preconditions(*, open_pr_count: int, is_dirty: bool, admin_session_confirmed: bool) -> None:
    require_zero_open_prs(open_pr_count)
    require_clean_guard(is_dirty)
    require_admin_session(admin_session_confirmed)


def is_git_dirty(root: Path = ROOT) -> bool:
    cmd = ["git", "-C", str(root), "status", "--porcelain"]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return bool(result.stdout.strip())


def hold_ids(policy: dict[str, Any]) -> set[str]:
    return {entry["id"] for entry in policy.get("holds", [])}


def verify_holds_refiled(source_policy: dict[str, Any], exported_policy: dict[str, Any] | None) -> list[str]:
    """Hold ids missing from the export (empty == every hold, incl. rd-2657, re-filed)."""
    required = hold_ids(source_policy)
    present = set() if exported_policy is None else hold_ids(exported_policy)
    return sorted(required - present)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_required_checks_plan(checks_policy: dict[str, Any]) -> list[dict[str, str]]:
    return [{"context": entry["context"]} for entry in checks_policy.get("required_contexts", [])]


def build_merge_queue_plan(checks_policy: dict[str, Any]) -> dict[str, Any]:
    return dict(checks_policy.get("merge_queue", {}).get("parameters", {}))


def build_settings_plan(settings: dict[str, Any], checks_policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "license": settings["license"],
        "actions_permissions": settings["actions_permissions"],
        "variables": list(settings.get("variables", [])),
        "environments": list(settings.get("environments", [])),
        "required_status_checks": build_required_checks_plan(checks_policy),
        "merge_queue": build_merge_queue_plan(checks_policy),
    }


@dataclass(frozen=True)
class ExportCheckResult:
    publish_findings: tuple[str, ...]
    secret_findings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.publish_findings and not self.secret_findings


def _publish_guard() -> Any:  # local import: keeps this module importable without the frontend toolchain
    sys.path.insert(0, str(ROOT / "scripts"))
    import publish_guard  # noqa: PLC0415

    return publish_guard


def exported_paths(root: Path, allowlist_path: Path | None = None) -> list[str]:
    guard = _publish_guard()
    rules = guard.load_rules(allowlist=allowlist_path) if allowlist_path else guard.load_rules()
    return [p for p in guard.tracked_files(root) if guard._matches_any(p, rules.allow)]  # noqa: SLF001


def check_export(
    root: Path,
    paths: list[str],
    *,
    secret_scanner: Callable[[Path], list[dict[str, Any]]] | None = None,
) -> ExportCheckResult:
    guard = _publish_guard()
    publish = guard.check(root, paths, guard.load_rules())
    scan = secret_scanner or (lambda scan_root: __import__("scan_history")._scan_gitleaks(scan_root, no_git=True))  # noqa: SLF001
    secrets = scan(root)
    return ExportCheckResult(
        publish_findings=tuple(f.render() for f in publish),
        secret_findings=tuple(s.get("rule", "secret") for s in secrets),
    )


def _github_api(method: str, url: str, *, token: str | None, body: dict[str, Any] | None = None) -> Any:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} failed ({exc.code}): {detail}") from exc


def fetch_open_pr_count(repo: str, token: str | None, *, github_get: GitHubGet = _github_api) -> int:
    data = github_get("GET", f"https://api.github.com/search/issues?q=repo:{repo}+type:pr+state:open", token=token)
    return int(data.get("total_count", 0))


def diff_names(desired: list[str], current: list[str]) -> dict[str, list[str]]:
    desired_set, current_set = set(desired), set(current)
    return {"add": sorted(desired_set - current_set), "remove": sorted(current_set - desired_set)}


def dry_run(
    *,
    settings_path: Path = DEFAULT_SETTINGS,
    checks_policy_path: Path = DEFAULT_CHECKS_POLICY,
    repo: str,
    token: str | None,
    github_get: GitHubGet = _github_api,
) -> dict[str, Any]:
    plan = build_settings_plan(load_json(settings_path), load_json(checks_policy_path))
    rulesets = github_get("GET", f"https://api.github.com/repos/{repo}/rulesets", token=token)
    variables = github_get("GET", f"https://api.github.com/repos/{repo}/actions/variables", token=token)
    environments = github_get("GET", f"https://api.github.com/repos/{repo}/environments", token=token)
    current = {
        "rulesets": [item.get("name") for item in (rulesets or [])],
        "variables": [item.get("name") for item in (variables or {}).get("variables", [])],
        "environments": [item.get("name") for item in (environments or {}).get("environments", [])],
    }
    return {
        "plan": plan,
        "current": current,
        "diff": {
            "variables": diff_names(plan["variables"], current["variables"]),
            "environments": diff_names([e["name"] for e in plan["environments"]], current["environments"]),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="D-sorganization/Runner_Dashboard")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--checks-policy", type=Path, default=DEFAULT_CHECKS_POLICY)
    parser.add_argument("--holds-policy", type=Path, default=DEFAULT_HOLDS_POLICY)
    parser.add_argument("--apply", action="store_true", help="apply mode (owner-run only); default is dry-run")
    args = parser.parse_args(argv)

    holds_policy = load_json(args.holds_policy)
    paths = exported_paths(ROOT)
    holds_rel_path = args.holds_policy.relative_to(ROOT).as_posix()
    missing_holds = verify_holds_refiled(holds_policy, holds_policy if holds_rel_path in paths else None)
    if missing_holds:
        for hold in missing_holds:
            print(f"::error::hold {hold} is not re-filed in the export", file=sys.stderr)
        return 1

    export_result = check_export(ROOT, paths)
    if not export_result.ok:
        for finding in (*export_result.publish_findings, *export_result.secret_findings):
            print(f"::error::export check: {finding}", file=sys.stderr)
        return 1

    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not args.apply:
        result = dry_run(
            settings_path=args.settings, checks_policy_path=args.checks_policy, repo=args.repo, token=token
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    try:
        apply_preconditions(
            open_pr_count=fetch_open_pr_count(args.repo, token),
            is_dirty=is_git_dirty(),
            admin_session_confirmed=os.getenv("RD_ADMIN_SESSION_CONFIRMED") == "1",
        )
    except PreconditionError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    print("apply preconditions satisfied; settings creation on the new repo is a separate owner-run admin step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
