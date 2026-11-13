#!/usr/bin/env python3
"""Cancel superseded PR and dequeued merge_group workflow runs via GitHub API."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared_scripts.github_workflow_reaper import (  # noqa: E402
    RunSnapshot,
    run_snapshot_from_api,
    select_cancellable_runs,
)
from shared_scripts.merge_queue_cancel_safety import (  # noqa: E402
    MergeQueueEntry,
    MergeQueueParseError,
    merge_queue_entries_from_graphql,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("reap_github_workflow_runs")

MERGE_QUEUE_GRAPHQL = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    mergeQueue {
      entries(first: 100) {
        totalCount
        pageInfo { hasNextPage }
        nodes {
          state
          headCommit { oid }
        }
      }
    }
  }
}
"""


def _str_to_bool(val: str) -> bool:
    return val.lower() in ("true", "1", "yes")


def _github_request(
    token: str,
    method: str,
    url: str,
    body: dict | None = None,
) -> tuple[int, bytes]:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def fetch_merge_queue_entries(token: str, owner: str, repo: str) -> list[MergeQueueEntry]:
    status, raw = _github_request(
        token,
        "POST",
        "https://api.github.com/graphql",
        {
            "query": MERGE_QUEUE_GRAPHQL,
            "variables": {"owner": owner, "name": repo},
        },
    )
    if status != 200:
        raise RuntimeError(f"mergeQueue GraphQL failed with HTTP {status}: {raw[:500]!r}")
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("errors"):
        raise RuntimeError(f"mergeQueue GraphQL errors: {payload['errors']}")
    try:
        return merge_queue_entries_from_graphql(payload.get("data") or {})
    except MergeQueueParseError as exc:
        raise RuntimeError(str(exc)) from exc


def fetch_workflow_runs(token: str, owner: str, repo: str, status: str) -> list[RunSnapshot]:
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs?status={status}&per_page=100"
    status_code, raw = _github_request(token, "GET", url)
    if status_code != 200:
        raise RuntimeError(f"list workflow runs ({status}) failed HTTP {status_code}: {raw[:500]!r}")
    data = json.loads(raw.decode("utf-8"))
    return [run_snapshot_from_api(r) for r in data.get("workflow_runs", [])]


def fetch_pr_metadata(token: str, owner: str, repo: str, pr_number: int) -> dict[str, str]:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    status_code, raw = _github_request(token, "GET", url)
    if status_code != 200:
        raise RuntimeError(f"pull request #{pr_number} fetch failed HTTP {status_code}")
    pr = json.loads(raw.decode("utf-8"))
    state = "merged" if pr.get("merged") else str(pr.get("state") or "")
    head_sha = str((pr.get("head") or {}).get("sha") or "")
    return {"state": state, "head_sha": head_sha}


def cancel_run(token: str, owner: str, repo: str, run_id: int) -> None:
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/cancel"
    status_code, raw = _github_request(token, "POST", url)
    if status_code not in (200, 201, 202):
        raise RuntimeError(f"cancel run {run_id} failed HTTP {status_code}: {raw[:300]!r}")


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Reap superseded GitHub workflow runs")
    parser.add_argument("--dry-run", type=str, default="true")
    parser.add_argument("--min-age-minutes", type=int, default=20)
    parser.add_argument("--max-cancel", type=int, default=10)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token.strip():
        logger.error("GITHUB_TOKEN (or GH_TOKEN) is required")
        sys.exit(1)

    repo_full = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repo_full:
        logger.error("GITHUB_REPOSITORY must be set to owner/repo")
        sys.exit(1)
    owner, repo = repo_full.split("/", 1)

    dry_run = _str_to_bool(args.dry_run)
    min_age = args.min_age_minutes
    max_cancel = max(0, args.max_cancel)
    now = datetime.now(timezone.utc)

    try:
        merge_entries = fetch_merge_queue_entries(token, owner, repo)
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    logger.info("Loaded %d live merge-queue entries", len(merge_entries))

    runs: list[RunSnapshot] = []
    for status in ("queued", "in_progress"):
        runs.extend(fetch_workflow_runs(token, owner, repo, status))

    pr_numbers = {r.pull_request_number for r in runs if r.pull_request_number is not None}
    pr_metadata: dict[int, dict[str, str]] = {}
    for number in sorted(pr_numbers):
        if number is None:
            continue
        try:
            pr_metadata[number] = fetch_pr_metadata(token, owner, repo, number)
        except RuntimeError as exc:
            logger.error("%s", exc)
            sys.exit(1)

    candidates = select_cancellable_runs(
        runs,
        merge_queue_entries=merge_entries,
        pr_metadata=pr_metadata,
    )

    aged: list[RunSnapshot] = []
    for run in candidates:
        created = _parse_ts(run.created_at)
        if created is None:
            continue
        age_minutes = (now - created).total_seconds() / 60.0
        if age_minutes >= min_age:
            aged.append(run)

    to_cancel = aged[:max_cancel] if max_cancel else aged
    logger.info(
        "Found %d cancellable runs (%d past min age); dry_run=%s",
        len(candidates),
        len(aged),
        dry_run,
    )

    cancelled = 0
    for run in to_cancel:
        if dry_run:
            logger.info("Would cancel run %s (%s %s)", run.run_id, run.event, run.head_branch)
            continue
        try:
            cancel_run(token, owner, repo, run.run_id)
            cancelled += 1
            logger.info("Cancelled run %s", run.run_id)
        except RuntimeError as exc:
            logger.error("%s", exc)
            sys.exit(1)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(
                f"## GitHub Workflow Reaper\n\n- dry_run={dry_run}\n"
                f"- merge_queue_entries={len(merge_entries)}\n"
                f"- cancellable={len(candidates)} past_min_age={len(aged)}\n"
                f"- cancelled={cancelled}\n"
            )


if __name__ == "__main__":
    main()
