"""Shared CI workflow concurrency helpers (issues #2764, #2768).

merge_group runs use a unique concurrency group per queue entry (head ref), never
``cancel-in-progress``. Superseded merge_group runs are cancelled only after an
explicit mergeQueue.entries check (``merge_queue_cancel_safety``), not via
workflow concurrency.
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
    "github.event_name == 'merge_group' && (github.event.merge_group.head_ref || github.ref || github.run_id) || "
    "format('{0}-{1}', github.event_name, github.sha) "
    "}}"
)

CANCEL_IN_PROGRESS_EXPR = "${{ github.event_name == 'pull_request' }}"

# CI Standard only: post-merge main pushes share one group so superseded
# runs cancel and release fleet slots. #2491 keeps per-SHA groups for deploy-blocking
# workflows (frontend-tests, docker-build, …) so auto-deploy can finish one commit.
CI_STANDARD_WORKFLOW_CONCURRENCY_GROUP_EXPR = (
    "${{ github.workflow }}-${{ "
    "github.event_name == 'pull_request' && github.ref || "
    "github.event_name == 'merge_group' && (github.event.merge_group.head_ref || github.ref || github.run_id) || "
    "github.event_name == 'push' && github.ref == 'refs/heads/main' && 'main' || "
    "format('{0}-{1}', github.event_name, github.sha) "
    "}}"
)

CI_STANDARD_CANCEL_IN_PROGRESS_EXPR = (
    "${{ github.event_name == 'pull_request' || "
    "github.event_name == 'push' && github.ref == 'refs/heads/main' }}"
)


def normalize_queue_head_ref(ref: str) -> str:
    """Normalize merge-queue head refs for comparison."""
    value = (ref or "").strip()
    if value.startswith("refs/heads/"):
        return value.removeprefix("refs/heads/")
    return value


def pr_number_from_queue_ref(ref: str) -> int | None:
    """PR number from a merge-queue head ref, else None."""
    match = QUEUE_REF_PATTERN.match(normalize_queue_head_ref(ref))
    return int(match.group("number")) if match else None


def merge_group_concurrency_token(head_ref: str) -> str | None:
    """Legacy PR-scoped token (not used for workflow concurrency groups)."""
    number = pr_number_from_queue_ref(head_ref)
    return f"merge_group-pr-{number}" if number is not None else None
