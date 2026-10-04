"""Pure parsing of GitHub webhook events into CI-fix triggers (#1881, #1879)."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import pytest
from ci_fix_events import parse_github_event, verify_github_signature

SECRET = "test-webhook-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


REPO = {
    "name": "Runner_Dashboard",
    "full_name": "D-sorganization/Runner_Dashboard",
    "owner": {"login": "D-sorganization"},
}


def _workflow_run(
    *,
    conclusion: str = "failure",
    prs: list[dict[str, Any]] | None = None,
    head_branch: str = "feat/x",
    event: str = "pull_request",
) -> dict[str, Any]:
    return {
        "action": "completed",
        "repository": REPO,
        "workflow_run": {
            "id": 555,
            "name": "CI Standard",
            "event": event,
            "conclusion": conclusion,
            "head_branch": head_branch,
            "pull_requests": [{"number": 42, "head": {"ref": "feat/x"}, "base": {"ref": "main"}}]
            if prs is None
            else prs,
        },
    }


def _dequeued(reason: str, *, draft: bool = False) -> dict[str, Any]:
    return {
        "action": "dequeued",
        "reason": reason,
        "repository": REPO,
        "pull_request": {
            "number": 77,
            "state": "open",
            "draft": draft,
            "auto_merge": None,
            "head": {"ref": "feat/conflicted"},
            "base": {"ref": "main"},
        },
    }


QUEUE_REF = "gh-readonly-queue/main/pr-77-0123456789abcdef0123456789abcdef01234567"


# ─── pure parsing ────────────────────────────────────────────────────────────


def test_signature_verification() -> None:
    body = b'{"a": 1}'
    assert verify_github_signature(body, _sign(body), SECRET) is True
    assert verify_github_signature(body, _sign(body, "other"), SECRET) is False
    assert verify_github_signature(body, None, SECRET) is False
    assert verify_github_signature(body, hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(), SECRET) is False


def test_parse_workflow_run_failure_on_pr() -> None:
    event, reason = parse_github_event("workflow_run", _workflow_run())
    assert event is not None, reason
    assert (event.kind, event.repo, event.pr_number, event.run_id) == ("ci_failure", "Runner_Dashboard", 42, 555)
    assert event.from_queue is False


def test_parse_merge_group_failure_recovers_pr_from_queue_ref() -> None:
    event, reason = parse_github_event(
        "workflow_run", _workflow_run(prs=[], head_branch=QUEUE_REF, event="merge_group")
    )
    assert event is not None, reason
    assert event.pr_number == 77
    assert event.from_queue is True
    assert event.base_ref == "main"


@pytest.mark.parametrize("conclusion", ["success", "cancelled", "skipped"])
def test_parse_ignores_non_failures(conclusion: str) -> None:
    event, reason = parse_github_event("workflow_run", _workflow_run(conclusion=conclusion))
    assert event is None
    assert conclusion in reason


def test_parse_ignores_run_without_pr() -> None:
    event, reason = parse_github_event("workflow_run", _workflow_run(prs=[], head_branch="main", event="push"))
    assert event is None
    assert "pull request" in reason


def test_parse_dequeue_reasons() -> None:
    conflict, _ = parse_github_event("pull_request", _dequeued("MERGE_CONFLICT"))
    failed, _ = parse_github_event("pull_request", _dequeued("CHECKS_FAILED"))
    manual, reason = parse_github_event("pull_request", _dequeued("MANUAL"))
    assert conflict is not None and conflict.kind == "merge_conflict" and conflict.pr_number == 77
    assert failed is not None and failed.kind == "queue_checks_failed" and failed.from_queue is True
    assert manual is None and "MANUAL" in reason


def test_parse_ignores_check_suite_other_events_and_foreign_org() -> None:
    assert parse_github_event("check_suite", {"action": "completed", "repository": REPO})[0] is None
    assert parse_github_event("pull_request", {**_dequeued("MERGE_CONFLICT"), "action": "opened"})[0] is None
    foreign = {**_workflow_run(), "repository": {**REPO, "owner": {"login": "someone-else"}}}
    event, reason = parse_github_event("workflow_run", foreign)
    assert event is None and "org" in reason
