"""Unit tests for shared staff GitHub formal-review helpers."""

from __future__ import annotations

from shared_scripts.independent_exact_head_review import RUNNER_DASHBOARD_BOT_LOGIN
from shared_scripts.staff_github_pr_review import (
    formal_github_review_event_for_verdict,
    own_stale_changes_requested_review_ids,
    runner_dashboard_has_approved_at_head,
)

_HEAD = "cccccccccccccccccccccccccccccccccccccccc"
_OLD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_BOT = {"login": RUNNER_DASHBOARD_BOT_LOGIN}
_HUMAN = {"login": "dieterolson"}


def test_formal_event_for_pass_and_approve() -> None:
    assert formal_github_review_event_for_verdict("PASS") == "APPROVE"
    assert formal_github_review_event_for_verdict("approve") == "APPROVE"
    assert formal_github_review_event_for_verdict("FIX-NEEDED") is None


def test_own_stale_changes_requested_ignores_humans_and_current_head() -> None:
    reviews = [
        {"id": 1, "state": "CHANGES_REQUESTED", "commit_id": _OLD, "user": _BOT},
        {"id": 2, "state": "CHANGES_REQUESTED", "commit_id": _HEAD, "user": _BOT},
        {"id": 3, "state": "CHANGES_REQUESTED", "commit_id": _OLD, "user": _HUMAN},
        {"id": 4, "state": "APPROVED", "commit_id": _HEAD, "user": _BOT},
    ]
    assert own_stale_changes_requested_review_ids(reviews, head_sha=_HEAD) == [1]


def test_runner_dashboard_has_approved_at_head() -> None:
    reviews = [
        {"id": 1, "state": "APPROVED", "commit_id": _HEAD[:7], "user": _BOT},
        {"id": 2, "state": "APPROVED", "commit_id": _OLD, "user": _BOT},
    ]
    assert runner_dashboard_has_approved_at_head(reviews, head_sha=_HEAD) is True
    assert runner_dashboard_has_approved_at_head(reviews[:1], head_sha=_OLD) is False