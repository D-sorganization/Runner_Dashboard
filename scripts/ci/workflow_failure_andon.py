#!/usr/bin/env python3
"""File a deduplicated Andon issue when CI Standard or CI Nightly fails on main paths (#4299)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if (_REPO_ROOT / "backend").is_dir() and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.andon.intake import AndonIntakeDeps, process_andon_intake  # noqa: E402

_ALERT_EVENTS = frozenset({"push", "schedule", "merge_group", "workflow_dispatch"})


def should_alert(event_name: str) -> bool:
    """Return whether this GitHub event should raise an operator-visible alert."""
    return event_name in _ALERT_EVENTS


def build_workflow_failure_payload(
    *,
    repo: str,
    workflow: str,
    event_name: str,
    run_id: str,
    head_sha: str,
    run_url: str | None = None,
) -> dict[str, str]:
    """Shape an A1 Andon payload for a failed workflow run."""
    stable = f"{workflow} reported failure on {event_name}"
    audit = f"run_id={run_id} head_sha={head_sha}"
    if run_url:
        audit = f"{audit} url={run_url}"
    return {
        "repo": repo,
        "workflow": workflow,
        "check": "workflow",
        "file": ".github/workflows",
        "error": f"{stable} ({audit})",
    }


@dataclass
class _GhIssueClient:
    gh_bin: str
    repo: str

    def list_open(self, repo: str) -> list[dict[str, Any]]:
        proc = subprocess.run(
            [
                self.gh_bin,
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--label",
                "andon",
                "--limit",
                "100",
                "--json",
                "number,body,state,labels",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "gh issue list failed")
        rows = json.loads(proc.stdout or "[]")
        return rows if isinstance(rows, list) else []

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict[str, Any]:
        proc = subprocess.run(
            [
                self.gh_bin,
                "issue",
                "create",
                "--repo",
                repo,
                "--title",
                title,
                "--body",
                body,
                "--label",
                ",".join(labels),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "gh issue create failed")
        url = proc.stdout.strip()
        number = int(url.rsplit("/", 1)[-1])
        return {"number": number, "repo": repo, "title": title, "body": body, "labels": labels, "state": "open"}

    def comment_issue(self, repo: str, issue_number: int, body: str) -> dict[str, Any]:
        proc = subprocess.run(
            [self.gh_bin, "issue", "comment", str(issue_number), "--repo", repo, "--body", body],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "gh issue comment failed")
        return {"repo": repo, "issue_number": issue_number, "body": body}


def gh_andon_deps(gh_bin: str, repo: str) -> AndonIntakeDeps:
    client = _GhIssueClient(gh_bin=gh_bin, repo=repo)
    return AndonIntakeDeps(
        list_open_andons=client.list_open,
        create_andon_issue=client.create_issue,
        comment_on_andon_issue=client.comment_issue,
    )


def file_workflow_failure_andon(
    payload: dict[str, str],
    *,
    deps: AndonIntakeDeps | None = None,
    gh_bin: str = "gh",
) -> dict[str, Any]:
    """File or dedupe an Andon issue for ``payload``."""
    active = deps if deps is not None else gh_andon_deps(gh_bin, payload["repo"])
    return process_andon_intake(payload, deps=active)


def _env_or(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", default=_env_or("GITHUB_EVENT_NAME"))
    parser.add_argument("--repo", default=_env_or("GITHUB_REPOSITORY"))
    parser.add_argument("--workflow", default=_env_or("GITHUB_WORKFLOW"))
    parser.add_argument("--run-id", default=_env_or("GITHUB_RUN_ID"))
    parser.add_argument("--sha", default=_env_or("GITHUB_SHA"))
    parser.add_argument("--run-url", default="")
    parser.add_argument("--gh", default="gh")
    parser.add_argument(
        "--force",
        action="store_true",
        help="File even when the event would normally be suppressed (tests only)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.force and not should_alert(args.event):
        print(f"Skipping Andon alert for event={args.event!r} (PR-tier noise is intentional).")
        return 0
    required_fields = (
        ("repo", args.repo),
        ("workflow", args.workflow),
        ("run-id", args.run_id),
        ("sha", args.sha),
    )
    for required, value in required_fields:
        if not value:
            print(f"Missing required --{required}", file=sys.stderr)
            return 2

    run_url = args.run_url.strip()
    if not run_url and args.repo and args.run_id:
        run_url = f"https://github.com/{args.repo}/actions/runs/{args.run_id}"

    payload = build_workflow_failure_payload(
        repo=args.repo,
        workflow=args.workflow,
        event_name=args.event,
        run_id=args.run_id,
        head_sha=args.sha,
        run_url=run_url or None,
    )
    result = file_workflow_failure_andon(payload, gh_bin=args.gh)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
