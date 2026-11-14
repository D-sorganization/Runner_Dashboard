"""Helpers for Runner Dashboard App formal PR reviews vs issue-comment verdicts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from shared_scripts.independent_exact_head_review import (
    RUNNER_DASHBOARD_BOT_LOGIN,
    head_sha_matches,
)


def review_user_login(item: Mapping[str, Any]) -> str:
    user = item.get("user")
    if isinstance(user, Mapping):
        return str(user.get("login") or "").strip()
    return ""


def is_runner_dashboard_bot_review(item: Mapping[str, Any], *, bot_login: str = RUNNER_DASHBOARD_BOT_LOGIN) -> bool:
    login = review_user_login(item)
    return bool(login) and login.lower() == bot_login.lower()


def formal_github_review_event_for_verdict(verdict: str) -> str | None:
    """Return the GitHub review ``event`` for a hub verdict, or None when comment-only."""
    token = (verdict or "").strip().lower()
    if token in {"pass", "approve", "approved"}:
        return "APPROVE"
    return None


def own_stale_changes_requested_review_ids(
    reviews: Sequence[Mapping[str, Any]],
    *,
    head_sha: str,
    bot_login: str = RUNNER_DASHBOARD_BOT_LOGIN,
) -> list[int]:
    """IDs of this App's CHANGES_REQUESTED reviews that are not bound to ``head_sha``."""
    out: list[int] = []
    for item in reviews:
        if str(item.get("state") or "").upper() != "CHANGES_REQUESTED":
            continue
        if not is_runner_dashboard_bot_review(item, bot_login=bot_login):
            continue
        commit = str(item.get("commit_id") or "").strip()
        if commit and head_sha_matches(commit, head_sha):
            continue
        rid = item.get("id")
        if isinstance(rid, int):
            out.append(rid)
    return out


def runner_dashboard_has_approved_at_head(
    reviews: Sequence[Mapping[str, Any]],
    *,
    head_sha: str,
    bot_login: str = RUNNER_DASHBOARD_BOT_LOGIN,
) -> bool:
    for item in reviews:
        if str(item.get("state") or "").upper() != "APPROVED":
            continue
        if not is_runner_dashboard_bot_review(item, bot_login=bot_login):
            continue
        commit = str(item.get("commit_id") or "").strip()
        if commit and head_sha_matches(commit, head_sha):
            return True
    return False