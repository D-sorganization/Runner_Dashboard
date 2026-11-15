"""Publish a durable GitHub notice when tier-1 CI fails (#4299).

Called from workflow failure hooks on schedule, main push and merge_group. PR
supersession cancels are filtered in the workflow ``if``; this script only
creates or updates a tracking issue.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

FAILURE_LABEL = "ci"
TITLE_PREFIX = "[CI failure]"


def should_publish_notice(event_name: str, ref: str) -> bool:
    """Return whether this event/ref pair should open or update a failure issue."""
    if event_name == "schedule":
        return True
    if event_name == "push" and ref == "refs/heads/main":
        return True
    if event_name == "merge_group":
        return True
    return False


def build_notice_title(workflow: str, ref: str) -> str:
    return f"{TITLE_PREFIX} {workflow} on {ref}"


def build_notice_body(
    *,
    workflow: str,
    ref: str,
    run_id: str,
    repository: str,
    run_url: str,
) -> str:
    return (
        f"Automated notice from `{workflow}` ({ref}).\n\n"
        f"- Repository: `{repository}`\n"
        f"- Run: {run_url}\n"
        f"- Run id: `{run_id}`\n\n"
        "Investigate the failing required checks on this run. "
        "Comment on this issue when the pipeline is green again."
    )


SearchOpen = Callable[..., list[dict[str, Any]]]
CreateIssue = Callable[..., int]
CommentIssue = Callable[[int, str], None]


@dataclass(frozen=True)
class NoticePublisher:
    search_open: SearchOpen
    create_issue: CreateIssue
    comment_issue: CommentIssue

    def publish(
        self,
        *,
        workflow: str,
        ref: str,
        run_id: str,
        repository: str,
        run_url: str,
    ) -> int:
        title = build_notice_title(workflow, ref)
        body = build_notice_body(
            workflow=workflow,
            ref=ref,
            run_id=run_id,
            repository=repository,
            run_url=run_url,
        )
        matches = self.search_open(repo=repository, title=title)
        if matches:
            issue_number = int(matches[0]["number"])
            self.comment_issue(issue_number, f"Another failing run: {run_url}\n\n{body}")
            return issue_number
        return int(
            self.create_issue(
                repo=repository,
                title=title,
                body=body,
                labels=[FAILURE_LABEL],
            )
        )


def _gh_json(cmd: list[str]) -> Any:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("gh failed: %s", result.stderr.strip())
        raise SystemExit(result.returncode)
    return json.loads(result.stdout or "null")


def _default_search_open(*, repo: str, title: str) -> list[dict[str, Any]]:
    query = f'repo:{repo} is:issue is:open label:{FAILURE_LABEL} "{title}" in:title'
    payload = _gh_json(["gh", "search", "issues", query, "--json", "number,title", "--limit", "5"])
    if not isinstance(payload, list):
        return []
    return [issue for issue in payload if issue.get("title") == title]


def _default_create_issue(*, repo: str, title: str, body: str, labels: list[str]) -> int:
    cmd = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--body",
        body,
    ]
    for label in labels:
        cmd.extend(["--label", label])
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("issue create failed: %s", result.stderr.strip())
        raise SystemExit(result.returncode)
    url = (result.stdout or "").strip()
    number = int(url.rsplit("/", 1)[-1])
    return number


def _default_comment_issue(issue_number: int, body: str) -> None:
    cmd = ["gh", "issue", "comment", str(issue_number), "--body", body]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("issue comment failed: %s", result.stderr.strip())
        raise SystemExit(result.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repository", default="D-sorganization/Runner_Dashboard")
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--event-name", default="")
    args = parser.parse_args(argv)
    if args.event_name and not should_publish_notice(args.event_name, args.ref):
        logger.info("skip publish for event=%s ref=%s", args.event_name, args.ref)
        return 0
    publisher = NoticePublisher(
        search_open=_default_search_open,
        create_issue=_default_create_issue,
        comment_issue=_default_comment_issue,
    )
    issue = publisher.publish(
        workflow=args.workflow,
        ref=args.ref,
        run_id=args.run_id,
        repository=args.repository,
        run_url=args.run_url,
    )
    print(f"published ci failure notice on issue #{issue}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())