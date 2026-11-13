"""CLI: scheduled fleet-wide open-PR line-cap scan (RD#3643). Read-only; one
batched GraphQL call across every fleet repository."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fleet_merge_checker import DEFAULT_POLICY_PATH, load_policy  # noqa: E402
from staff.fleet_pr_line_cap_scan import (  # noqa: E402
    LINE_CAP,
    ScanReport,
    build_graphql_query,
    build_report,
    parse_scan_response,
)

_RUNNER = Callable[..., subprocess.CompletedProcess[str]]


def load_fleet_repos(policy_path: Path) -> list[str]:
    policy = load_policy(policy_path)
    repos = policy.get("repositories")
    if not isinstance(repos, list) or not repos or not all(isinstance(r, str) for r in repos):
        raise ValueError(f"{policy_path} has no non-empty 'repositories' list")
    return repos


def fetch_scan_payload(repos: Sequence[str], *, run: _RUNNER = subprocess.run) -> dict[str, object]:
    query = build_graphql_query(repos)
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    response = run(cmd, capture_output=True, text=True, check=False, timeout=60)
    if response.returncode != 0:
        raise RuntimeError(f"gh api graphql failed: {response.stderr.strip()}")
    payload = json.loads(response.stdout)
    if not isinstance(payload, dict):
        raise ValueError("gh api graphql returned a non-object response")
    return payload


def run_scan(repos: Sequence[str], *, cap: int = LINE_CAP, run: _RUNNER = subprocess.run) -> ScanReport:
    payload = fetch_scan_payload(repos, run=run)
    parsed = parse_scan_response(payload, repos)
    return build_report(parsed, cap=cap)


def main(argv: Sequence[str] | None = None, *, run: _RUNNER = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--cap", type=int, default=LINE_CAP)
    parser.add_argument("--repos", help="Comma-separated owner/name overrides; default reads the fleet policy.")
    args = parser.parse_args(argv)
    repos = [r.strip() for r in args.repos.split(",")] if args.repos else load_fleet_repos(args.policy_path)
    report = run_scan(repos, cap=args.cap, run=run)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
