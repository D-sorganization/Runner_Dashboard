"""CLI wrapper for fleet-wide merge queue and branch protection settings drift check.

Authority: Repository_Management#1890, Repository_Management#1900, Runner_Dashboard#1850.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Add backend directory to sys.path so fleet_merge_checker is importable
_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fleet_merge_checker import (  # noqa: E402
    DEFAULT_POLICY_PATH,
    check_fleet,
    fetch_live_repo_snapshot,
    format_drift_issue,
    load_policy,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--snapshots-dir", help="Directory containing <repo-slug>.json snapshot files")
    parser.add_argument("--live", action="store_true", help="Fetch live data via GitHub REST API")
    parser.add_argument("--repo", help="Check only this repository")
    parser.add_argument("--output-json", action="store_true", help="Output results as JSON")
    parser.add_argument("--format-issue", action="store_true", help="Format GitHub issue markdown on drift")
    args = parser.parse_args(argv)

    policy = load_policy(Path(args.policy))
    fleet_snapshots: dict[str, dict] = {}

    target_repos = [args.repo] if args.repo else policy.get("repositories", [])

    if args.live:
        token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
        for repo in target_repos:
            try:
                fleet_snapshots[repo] = fetch_live_repo_snapshot(repo, token)
            except Exception as exc:
                print(f"Error fetching live data for {repo}: {exc}", file=sys.stderr)
                fleet_snapshots[repo] = {
                    "repo": repo,
                    "repo_details": {},
                    "protection": {},
                    "rulesets": [],
                    "findings": [f"API fetch failed: {exc}"],
                }
    elif args.snapshots_dir:
        sdir = Path(args.snapshots_dir)
        for repo in target_repos:
            safe_name = repo.replace("/", "__") + ".json"
            spath = sdir / safe_name
            if spath.is_file():
                fleet_snapshots[repo] = json.loads(spath.read_text(encoding="utf-8"))
    else:
        parser.error("Either --live or --snapshots-dir is required")

    report = check_fleet(policy, fleet_snapshots)

    if args.output_json:
        print(json.dumps(report, indent=2))
    else:
        for res in report["results"]:
            repo = res["repo"]
            status = res["status"].upper()
            findings = res["findings"]
            if status == "PASS":
                print(f"[{status}] {repo}")
            else:
                print(f"[{status}] {repo}:", file=sys.stderr)
                for f in findings:
                    print(f"  - {f}", file=sys.stderr)
                if args.format_issue:
                    title, body = format_drift_issue(repo, findings)
                    print("\n--- Proposed GitHub Issue ---", file=sys.stderr)
                    print(f"Title: {title}", file=sys.stderr)
                    print(body, file=sys.stderr)
                    print("-----------------------------\n", file=sys.stderr)

    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
