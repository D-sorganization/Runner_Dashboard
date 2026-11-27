"""TDD tests for RD#4704 — independent approval link recorded before auto-arm."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SHA = "aabbccddeeff001122334455667788990011aabb"  # pragma: allowlist secret
_SHORT = _SHA[:12]
_COMMENT_URL = "https://github.com/D-sorganization/Runner_Dashboard/issues/9#issuecomment-9999"


def _make_comment(verdict: str = "PASS", sha: str = _SHORT, url: str = _COMMENT_URL) -> dict:
    return {
        "user": {"login": "d-sorg-runner-dashboard[bot]"},
        "body": (
            f"Independent Exact-Head Acceptance Review (independent_review / `gpt-99`)\n"
            f"{verdict} @{sha}\n"
        ),
        "created_at": "2026-11-27T00:00:00Z",
        "html_url": url,
    }


# ---------------------------------------------------------------------------
# 1. IndependentHeadReview carries html_url from the comment item
# ---------------------------------------------------------------------------


def test_parse_issue_comments_captures_html_url() -> None:
    from shared_scripts.independent_exact_head_review import parse_issue_comments

    reviews = parse_issue_comments([_make_comment()])
    assert len(reviews) == 1
    assert reviews[0].html_url == _COMMENT_URL


def test_parse_issue_comments_html_url_defaults_to_empty_string() -> None:
    """Items without html_url (edge case) should not crash; default to empty."""
    from shared_scripts.independent_exact_head_review import parse_issue_comments

    comment = _make_comment()
    del comment["html_url"]
    reviews = parse_issue_comments([comment])
    assert len(reviews) == 1
    assert reviews[0].html_url == ""


# ---------------------------------------------------------------------------
# 2. latest_independent_verdict_at_head returns the url as the detail string
# ---------------------------------------------------------------------------


def test_latest_verdict_detail_is_approval_link() -> None:
    from shared_scripts.independent_exact_head_review import (
        IndependentHeadReview,
        latest_independent_verdict_at_head,
    )

    review = IndependentHeadReview(
        verdict="PASS",
        head_sha=_SHORT,
        reviewer_model="gpt-99",
        created_at="2026-11-27T00:00:00Z",
        html_url=_COMMENT_URL,
    )
    verdict, detail = latest_independent_verdict_at_head(
        [review], head_sha=_SHA, author_model="claude-sonnet-4-6"
    )
    assert verdict == "PASS"
    assert detail == _COMMENT_URL


def test_latest_verdict_no_review_detail_is_sentinel() -> None:
    from shared_scripts.independent_exact_head_review import latest_independent_verdict_at_head

    verdict, detail = latest_independent_verdict_at_head(
        [], head_sha=_SHA, author_model="claude-sonnet-4-6"
    )
    assert verdict is None
    assert detail == "no_independent_verdict_at_head"


# ---------------------------------------------------------------------------
# 3. HoldVerdict carries approval_link
# ---------------------------------------------------------------------------


def test_hold_verdict_has_approval_link_field() -> None:
    from scripts.automerge_guard import HoldVerdict

    v = HoldVerdict(held=False, approval_link=_COMMENT_URL)
    assert v.approval_link == _COMMENT_URL


def test_hold_verdict_approval_link_defaults_empty() -> None:
    from scripts.automerge_guard import HoldVerdict

    v = HoldVerdict(held=False)
    assert v.approval_link == ""


# ---------------------------------------------------------------------------
# 4. evaluate_hold surfaces approval_link when PASS is recorded
# ---------------------------------------------------------------------------


def _make_raw_pr(sha: str = _SHA) -> dict:
    return {
        "number": 9,
        "draft": False,
        "labels": [{"name": "agent-model:claude-sonnet-4-6"}],
        "head": {"sha": sha, "ref": "feat/x", "repo": {"full_name": "D-sorg/RD"}},
        "base": {"ref": "main", "repo": {"full_name": "D-sorg/RD"}},
        "body": "",
        "node_id": "PR_x",
    }


def test_evaluate_hold_approval_link_populated_on_pass(monkeypatch) -> None:
    import scripts.automerge_guard as ag

    raw = _make_raw_pr()
    monkeypatch.setattr(ag, "_gh_json", lambda *a, **k: raw)
    monkeypatch.setattr(ag, "_disarm_reasons", lambda *a, **k: [])
    monkeypatch.setattr(ag, "_deletion_reasons", lambda *a, **k: [])
    monkeypatch.setattr(ag, "_closing_board_reasons", lambda *a, **k: [])

    comment = _make_comment(verdict="PASS", sha=_SHA[:12], url=_COMMENT_URL)
    monkeypatch.setattr(ag, "_issue_comments", lambda *a, **k: [comment])
    monkeypatch.setattr(ag, "_commit_messages", lambda *a, **k: [])

    verdict = ag.evaluate_hold("D-sorg/RD", 9, runner=MagicMock(return_value=MagicMock(returncode=0)))
    assert not verdict.held
    assert verdict.pass_head_sha == _SHA
    assert verdict.approval_link == _COMMENT_URL


# ---------------------------------------------------------------------------
# 5. arm_auto_merge logs the approval_link before arming
# ---------------------------------------------------------------------------


def test_arm_auto_merge_logs_approval_link(monkeypatch, caplog) -> None:
    import logging

    import scripts.automerge_guard as ag

    raw = _make_raw_pr()
    monkeypatch.setattr(ag, "_gh_json", lambda *a, **k: raw)
    monkeypatch.setattr(ag, "_disarm_reasons", lambda *a, **k: [])
    monkeypatch.setattr(ag, "_deletion_reasons", lambda *a, **k: [])
    monkeypatch.setattr(ag, "_closing_board_reasons", lambda *a, **k: [])

    comment = _make_comment(verdict="PASS", sha=_SHA[:12], url=_COMMENT_URL)
    monkeypatch.setattr(ag, "_issue_comments", lambda *a, **k: [comment])
    monkeypatch.setattr(ag, "_commit_messages", lambda *a, **k: [])

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = ""
    mock_proc.stderr = ""
    run = MagicMock(return_value=mock_proc)

    with caplog.at_level(logging.INFO, logger="scripts.automerge_guard"):
        ag.arm_auto_merge("D-sorg/RD", 9, runner=run)

    assert any(_COMMENT_URL in record.message for record in caplog.records), (
        "approval_link not logged before arm"
    )
