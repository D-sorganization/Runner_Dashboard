"""Post formal GitHub PR reviews as d-sorg-runner-dashboard[bot] (#hub-approve-clears-cr).

Pure helpers are mirrored from ``shared_scripts.staff_github_pr_review`` so the
deploy artifact can finalize code-reviewer runs with only ``backend/`` on ``sys.path``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from coordination.merge_shepherd_exact_head import head_sha_matches
from dashboard_config import ORG
from staff.independent_exact_head_verdict import (
    VerdictPostError,
    mint_runner_dashboard_verdict_token,
)

log = logging.getLogger("dashboard.staff.staff_github_pr_review")

RUNNER_DASHBOARD_BOT_LOGIN = "d-sorg-runner-dashboard[bot]"

_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def review_user_login(item: Mapping[str, Any]) -> str:
    user = item.get("user")
    if isinstance(user, Mapping):
        return str(user.get("login") or "").strip()
    return ""


def is_runner_dashboard_bot_review(item: Mapping[str, Any], *, bot_login: str = RUNNER_DASHBOARD_BOT_LOGIN) -> bool:
    login = review_user_login(item)
    return bool(login) and login.lower() == bot_login.lower()


def own_stale_changes_requested_review_ids(
    reviews: Sequence[Mapping[str, Any]],
    *,
    head_sha: str,
    bot_login: str = RUNNER_DASHBOARD_BOT_LOGIN,
) -> list[int]:
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


def _auth_headers(token: str) -> dict[str, str]:
    return {**_GITHUB_HEADERS, "Authorization": f"Bearer {token}"}


def fetch_pull_reviews(*, full_repo: str, pr_number: int, token: str) -> list[dict[str, Any]]:
    url = f"https://api.github.com/repos/{full_repo}/pulls/{int(pr_number)}/reviews?per_page=100"
    try:
        resp = httpx.get(url, headers=_auth_headers(token), timeout=httpx.Timeout(30.0))
    except httpx.HTTPError as exc:
        raise VerdictPostError(f"GitHub PR reviews list failed: {exc}") from exc
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()[:240]
        raise VerdictPostError(
            f"GitHub PR reviews list refused: HTTP {resp.status_code} ({detail})",
            http_status=resp.status_code,
        )
    payload = resp.json()
    if not isinstance(payload, list):
        raise VerdictPostError("GitHub PR reviews list returned unexpected payload")
    return [row for row in payload if isinstance(row, dict)]


def dismiss_pull_review(
    *,
    full_repo: str,
    pr_number: int,
    review_id: int,
    message: str,
    token: str,
) -> None:
    url = f"https://api.github.com/repos/{full_repo}/pulls/{int(pr_number)}/reviews/{int(review_id)}/dismissals"
    try:
        resp = httpx.put(
            url,
            json={"message": message, "event": "DISMISS"},
            headers=_auth_headers(token),
            timeout=httpx.Timeout(30.0),
        )
    except httpx.HTTPError as exc:
        raise VerdictPostError(f"GitHub review dismiss failed: {exc}") from exc
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()[:240]
        raise VerdictPostError(
            f"GitHub review dismiss refused: HTTP {resp.status_code} ({detail})",
            http_status=resp.status_code,
        )


def submit_pull_request_review(
    *,
    full_repo: str,
    pr_number: int,
    commit_id: str,
    event: str,
    body: str,
    token: str,
) -> None:
    url = f"https://api.github.com/repos/{full_repo}/pulls/{int(pr_number)}/reviews"
    try:
        resp = httpx.post(
            url,
            json={"commit_id": commit_id, "body": body, "event": event},
            headers=_auth_headers(token),
            timeout=httpx.Timeout(30.0),
        )
    except httpx.HTTPError as exc:
        raise VerdictPostError(f"GitHub formal review post failed: {exc}") from exc
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()[:240]
        raise VerdictPostError(
            f"GitHub formal review post refused: HTTP {resp.status_code} ({detail})",
            http_status=resp.status_code,
        )


def build_advisory_approve_review_body(*, pr_number: int, head_sha: str) -> str:
    short = head_sha.lower()[:7]
    return (
        f"Staff code-reviewer: **approve** PR #{pr_number} at `{short}` "
        f"(hub-posted formal review; see thread for findings)."
    )


def ensure_runner_dashboard_formal_approve(
    *,
    repo: str,
    pr_number: int,
    head_sha: str,
    body: str,
) -> None:
    """Submit APPROVE at ``head_sha`` and dismiss this App's stale CHANGES_REQUESTED reviews."""
    from staff.independent_exact_head_verdict import _repo_slug

    slug = _repo_slug(repo)
    full_repo = f"{ORG}/{slug}"
    sha = head_sha.lower()
    cred = mint_runner_dashboard_verdict_token(repo)
    reviews = fetch_pull_reviews(full_repo=full_repo, pr_number=pr_number, token=cred.token)
    if runner_dashboard_has_approved_at_head(reviews, head_sha=sha):
        log.info("Formal APPROVED review already recorded for %s#%s at %s", full_repo, pr_number, sha[:7])
        return
    dismiss_msg = f"Superseded by staff code-reviewer APPROVE at {sha[:7]}"
    for review_id in own_stale_changes_requested_review_ids(reviews, head_sha=sha):
        log.info("Dismissing stale CHANGES_REQUESTED review %s on %s#%s", review_id, full_repo, pr_number)
        dismiss_pull_review(
            full_repo=full_repo,
            pr_number=pr_number,
            review_id=review_id,
            message=dismiss_msg,
            token=cred.token,
        )
    submit_pull_request_review(
        full_repo=full_repo,
        pr_number=pr_number,
        commit_id=sha,
        event="APPROVE",
        body=body,
        token=cred.token,
    )
    log.info("Posted formal APPROVE for %s#%s at %s", full_repo, pr_number, sha[:7])