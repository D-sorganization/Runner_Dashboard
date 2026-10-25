"""Shared CI workflow concurrency helpers (issues #2764, #2768).

Pre: merge-queue refs match ``gh-readonly-queue/<base>/pr-<N>-<sha>`` (optional
``refs/heads/`` prefix) when deriving a PR-scoped merge_group token.
Post: ``merge_group_concurrency_token`` returns ``merge_group-pr-<N>`` or None
when the ref is not a merge-queue PR head (callers must fall back to run id).
"""

from __future__ import annotations

import re

QUEUE_REF_PATTERN = re.compile(
    r"^(?:refs/heads/)?gh-readonly-queue/(?P<base>.+)/pr-(?P<number>\d+)-(?P<sha>[0-9a-f]{7,40})$"
)

# Canonical workflow-level ``concurrency`` expressions. Workflows must use these
# verbatim; tests/test_ci_workflow_concurrency.py enforces that contract.
WORKFLOW_CONCURRENCY_GROUP_EXPR = (
    "${{ github.workflow }}-${{ "
    "github.event_name == 'pull_request' && github.ref || "
    "github.event_name == 'merge_group' && contains(github.event.merge_group.head_ref, '/pr-') && "
    "format('merge_group-pr-{0}', split(split(github.event.merge_group.head_ref, '/pr-')[1], '-')[0]) || "
    "github.event_name == 'merge_group' && (github.event.merge_group.head_ref || github.run_id) || "
    "format('{0}-{1}', github.event_name, github.sha) "
    "}}"
)

CANCEL_IN_PROGRESS_EXPR = "${{ github.event_name == 'pull_request' || github.event_name == 'merge_group' }}"


def pr_number_from_queue_ref(ref: str) -> int | None:
    """PR number from a merge-queue head ref, else None."""
    match = QUEUE_REF_PATTERN.match(ref or "")
    return int(match.group("number")) if match else None


def merge_group_concurrency_token(head_ref: str) -> str | None:
    """Concurrency token for one merge-queue PR, or None when unparseable."""
    number = pr_number_from_queue_ref(head_ref)
    return f"merge_group-pr-{number}" if number is not None else None
