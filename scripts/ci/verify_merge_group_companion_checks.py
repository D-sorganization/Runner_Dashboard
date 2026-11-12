"""Verify that required companion check-runs have succeeded on merge_group (#3450).

In Runner_Dashboard, Frontend Tests, CI Secrets, API Contract Regeneration,
and Docker Build & Trivy Scan run as companion workflows on merge_group.
Because the org-level ruleset only requires 'quality-gate', quality-gate
fails closed on merge_group if any companion check fails or skips.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_REPO = "D-sorganization/Runner_Dashboard"
REQUIRED_COMPANION_CHECKS: frozenset[str] = frozenset(
    {
        "frontend-required",
        "detect-secrets (baseline diff)",
        "api-contract-regeneration",
        "docker-build-scan",
    }
)


class PreconditionError(ValueError):
    """Raised when preconditions for check verification are violated."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify companion merge_group check runs.")
    parser.add_argument(
        "--repo",
        default=os.environ.get("REPOSITORY", DEFAULT_REPO),
        help="Target repository (owner/repo)",
    )
    parser.add_argument(
        "--sha",
        default=os.environ.get("RUN_HEAD_SHA", ""),
        help="Commit SHA to inspect check runs for",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", ""),
        help="GitHub token with checks/contents read permission",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("COMPANION_CHECK_TIMEOUT", "180")),
        help="Max seconds to wait for in-progress companion checks",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="Seconds between check-run API polls",
    )
    return parser.parse_args(argv)


def fetch_check_runs(repo: str, sha: str, token: str) -> list[dict[str, Any]]:
    url = f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs?per_page=100"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
            return list(data.get("check_runs", []))
    except urllib.error.HTTPError as exc:
        print(f"Warning: HTTP {exc.code} fetching check-runs: {exc.reason}", file=sys.stderr)
        return []
    except Exception as exc:
        print(f"Warning: failed to query check-runs API: {exc}", file=sys.stderr)
        return []


def evaluate_checks(
    check_runs: list[dict[str, Any]],
    required: frozenset[str],
) -> tuple[list[str], list[str]]:
    """Return (pending_list, failure_list)."""
    found_status: dict[str, tuple[str, str]] = {}
    for cr in check_runs:
        name = cr.get("name")
        if name in required:
            found_status[name] = (cr.get("status", ""), cr.get("conclusion") or "")

    pending: list[str] = []
    failed: list[str] = []
    for req_name in sorted(required):
        if req_name not in found_status:
            pending.append(f"{req_name} (not found yet)")
        else:
            status, conclusion = found_status[req_name]
            if status != "completed":
                pending.append(f"{req_name} (status={status})")
            elif conclusion != "success":
                failed.append(f"{req_name} (conclusion={conclusion})")

    return pending, failed


def verify_companion_checks(
    repo: str,
    sha: str,
    token: str,
    timeout: int = 180,
    poll_interval: int = 5,
) -> None:
    if not sha or not sha.strip():
        raise PreconditionError("Commit SHA is required to verify companion checks.")
    if not token or not token.strip():
        raise PreconditionError("GitHub token is required to verify companion checks.")

    deadline = time.time() + timeout
    print(f"Verifying companion checks for {repo}@{sha} (timeout: {timeout}s)...")

    while True:
        check_runs = fetch_check_runs(repo, sha, token)
        pending, failed = evaluate_checks(check_runs, REQUIRED_COMPANION_CHECKS)

        if failed:
            for item in failed:
                print(f"::error::Companion check {item} did not succeed on merge_group.", file=sys.stderr)
            sys.exit(1)

        if not pending:
            for req_name in sorted(REQUIRED_COMPANION_CHECKS):
                print(f"Companion check {req_name}: success")
            print("All required companion checks succeeded.")
            return

        if time.time() >= deadline:
            for item in pending:
                print(f"::error::Timed out waiting for companion check: {item}", file=sys.stderr)
            sys.exit(1)

        print(f"Waiting for companion checks on merge_group: {', '.join(pending)}...")
        time.sleep(poll_interval)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    verify_companion_checks(
        repo=args.repo,
        sha=args.sha,
        token=args.token,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
    )


if __name__ == "__main__":
    main()
