#!/usr/bin/env python3
"""Inventory repository secret exposure without printing secret values."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def findings_summary(raw: str) -> list[dict[str, Any]]:
    """Keep only stable references from a gitleaks JSON report."""
    findings = json.loads(raw or "[]")
    return [
        {
            "rule": item.get("RuleID", "unknown"),
            "file": item.get("File", "unknown"),
            "commit": item.get("Commit", "unknown"),
            "line": item.get("StartLine"),
        }
        for item in findings
        if isinstance(item, dict)
    ]


def validate_report_path(path: Path, repo: Path) -> Path:
    resolved = path.expanduser().resolve()
    root = repo.resolve()
    if resolved == root or root in resolved.parents:
        raise ValueError("report path must be outside the repository")
    return resolved


def _run(args: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        # Never forward external tool output: it may contain matched values.
        raise RuntimeError(f"command failed: {args[0]}")
    return result.stdout


def _repo_name(repo: Path) -> str:
    remote = _run(["git", "-C", str(repo), "config", "--get", "remote.origin.url"]).strip()
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+)(?:\.git)?$", remote)
    if not match:
        raise ValueError("origin must point to a GitHub repository")
    return match.group(1)


def _count_pages(endpoint: str) -> int:
    output = _run(["gh", "api", "--paginate", "--jq", "length", endpoint])
    try:
        return sum(int(line) for line in output.splitlines() if line.strip())
    except ValueError as exc:
        raise RuntimeError("GitHub inventory returned an invalid count") from exc


def github_inventory(repo_name: str) -> dict[str, int]:
    """Count metadata only; comment and artifact contents are never displayed or saved."""
    encoded = repo_name
    return {
        "actions_artifacts": _count_pages(f"repos/{encoded}/actions/artifacts?per_page=100"),
        "actions_runs": _count_pages(f"repos/{encoded}/actions/runs?per_page=100"),
        "forks": _count_pages(f"repos/{encoded}/forks?per_page=100"),
        "issue_and_pr_comments": _count_pages(f"repos/{encoded}/issues/comments?per_page=100"),
        "pull_request_review_comments": _count_pages(f"repos/{encoded}/pulls/comments?per_page=100"),
    }


def _scan_gitleaks(repo: Path, *, no_git: bool) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="rd-secret-scan-") as temp_dir:
        report = Path(temp_dir) / "gitleaks.json"
        command = [
            "gitleaks",
            "detect",
            "--source",
            str(repo),
            "--config",
            str(repo / ".gitleaks.toml"),
            "--redact",
            "--report-path",
            str(report),
            "--report-format",
            "json",
            "--exit-code",
            "1",
        ]
        if no_git:
            command.append("--no-git")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode not in (0, 1):
            raise RuntimeError("gitleaks scan failed")
        return findings_summary(report.read_text(encoding="utf-8") if report.exists() else "[]")


def scan_history(repo: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return _scan_gitleaks(repo, no_git=True), _scan_gitleaks(repo, no_git=False)


def render_report(
    tree_findings: list[dict[str, Any]],
    history_findings: list[dict[str, Any]],
    inventory: dict[str, int],
) -> str:
    def references(findings: list[dict[str, Any]]) -> str:
        return (
            "\n".join(
                f"- `{item['rule']}` — `{item['file']}` commit `{item['commit']}`, line {item['line']}"
                for item in findings
            )
            or "- No findings."
        )

    inventory_rows = "\n".join(f"- {name.replace('_', ' ')}: {count}" for name, count in inventory.items())
    return (
        "# Secret exposure inventory\n\n"
        f"## Tree scan\nPotential findings: {len(tree_findings)}\n\n{references(tree_findings)}\n\n"
        f"## Full history scan\nPotential findings: {len(history_findings)}\n\n{references(history_findings)}\n\n"
        "## Actions artifacts and logs\n"
        f"{inventory['actions_artifacts']} artifacts; "
        f"{inventory['actions_runs']} workflow runs inventoried by metadata.\n\n"
        "Configured log retention (days): owner confirmation required.\n\n"
        "## Forks\n"
        f"{inventory['forks']} forks inventoried by metadata.\n\n"
        "## Issue and pull request discussions\n"
        f"{inventory['issue_and_pr_comments']} issue/PR comments and "
        f"{inventory['pull_request_review_comments']} review comments inventoried by count.\n\n"
        "## Inventory counts\n"
        f"{inventory_rows}\n\n"
        "## Rotation list\n"
        "- Add only confirmed credential categories and rotation status; never record credential values.\n"
    )


def write_private_report(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise ValueError("report directory must be private (mode 0700 or stricter)")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository to scan")
    parser.add_argument("--report", type=Path, help="new report path outside the repo in a private directory")
    parser.add_argument("--dry-run", action="store_true", help="list scan targets without running tools")
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.dry_run:
        print("Would scan current tree and full git history with gitleaks.")
        print("Would count Actions artifacts/runs, forks, issue/PR comments and review comments via gh metadata.")
        print("Would write sanitized references and counts to a private report outside the repository.")
        return 0
    if not args.report:
        parser.error("--report is required unless --dry-run is used")
    report_path = validate_report_path(args.report, repo)
    try:
        tree_findings, history_findings = scan_history(repo)
        inventory = github_inventory(_repo_name(repo))
        write_private_report(report_path, render_report(tree_findings, history_findings, inventory))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Inventory failed: {exc}")
        return 1
    print(
        "Inventory complete: "
        f"{len(tree_findings) + len(history_findings)} potential finding reference(s); "
        f"{sum(inventory.values())} metadata records."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
