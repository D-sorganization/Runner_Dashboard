"""Create Code Request labels in target repositories (CR-2, issue #1282).

Creates or updates `code-request` and `code-request:<state>` labels across
configured fleet repositories using the GitHub CLI.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections.abc import Sequence

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ensure_code_request_labels")

DEFAULT_ORG = "D-sorganization"
DEFAULT_REPOS: tuple[str, ...] = (
    "Runner_Dashboard",
    "UpstreamDrift",
    "AffineDrift",
    "Gasification_Model",
    "Tools",
    "Tools_Private",
    "Repository_Management",
)

LABELS: list[tuple[str, str, str]] = [
    ("code-request", "1d76db", "Code Request tracking issue"),
    ("code-request:draft", "cfd3d7", "Code Request in draft status"),
    ("code-request:triage", "fef2c0", "Code Request pending triage"),
    ("code-request:board_review", "fbca04", "Code Request awaiting Architecture Board review"),
    ("code-request:planning", "bfdadc", "Code Request undergoing technical planning"),
    ("code-request:planned", "c2e0c6", "Code Request planned with epic/work breakdown"),
    ("code-request:executing", "0075ca", "Code Request actively executing on agent/runner"),
    ("code-request:done", "0e8a16", "Code Request successfully completed and verified"),
    ("code-request:failed", "d93f0b", "Code Request execution failed"),
    ("code-request:deferred", "e99695", "Code Request deferred by board or operator"),
    ("code-request:declined", "6a737d", "Code Request declined"),
    ("code-request:cancelled", "5319e7", "Code Request cancelled by operator"),
]


def ensure_labels_for_repo(org: str, repo: str, dry_run: bool = False) -> int:
    """Ensure all Code Request labels exist in a single repository."""
    target_repo = f"{org}/{repo}" if "/" not in repo else repo
    log.info("Ensuring labels for %s", target_repo)
    failures = 0

    for name, color, desc in LABELS:
        cmd = [
            "gh",
            "label",
            "create",
            name,
            "--repo",
            target_repo,
            "--color",
            color,
            "--description",
            desc,
            "--force",
        ]
        if dry_run:
            log.info("[dry-run] %s", " ".join(cmd))
            continue

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0:
                log.info("  ✓ %s (%s)", name, color)
            else:
                log.warning("  ✗ %s failed: %s", name, res.stderr.strip())
                failures += 1
        except OSError as err:
            log.error("Failed to execute gh CLI: %s", err)
            return 1

    return failures


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Ensure Code Request labels in GitHub repos")
    parser.add_argument("--org", default=DEFAULT_ORG, help=f"GitHub organization (default: {DEFAULT_ORG})")
    parser.add_argument(
        "--repo",
        action="append",
        dest="repos",
        help="Repository to update (can be specified multiple times; defaults to fleet repos)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing")

    args = parser.parse_args(argv)
    repos = args.repos or list(DEFAULT_REPOS)

    total_failures = 0
    for repo in repos:
        total_failures += ensure_labels_for_repo(args.org, repo, dry_run=args.dry_run)

    log.info("Label synchronization completed with %d failure(s).", total_failures)
    return 1 if total_failures > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
