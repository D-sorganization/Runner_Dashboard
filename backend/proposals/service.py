"""Business logic for Board Proposals (issue #1284, CR-7).

Handles validation, rate limiting, duplicate candidate detection,
consensus link resolution, and GitHub issue store orchestration.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from gh_utils import RateLimitedError
from identity import Principal
from priorities.sources import list_meetings, read_meeting
from proposals import store
from proposals.models import (
    CreateProposalRequest,
    ProposalComment,
    ProposalDetail,
    ProposalItem,
)
from staff.workspace import rm_root

log = logging.getLogger("dashboard.proposals.service")

DEFAULT_AGENT_PROPOSAL_LIMIT = 5
RM_REPO_URL = "https://github.com/D-sorganization/Repository_Management"
_STOP_WORDS = frozenset(
    {"the", "a", "an", "for", "in", "on", "of", "to", "and", "or", "is", "at", "by", "with", "from", "as"}
)
# A hidden marker recording who actually submitted a proposal, independent of
# the user-supplied `source` field. `source` is display-only and caller
# controlled (the fleet client sends whatever `self.agent` is); the rate
# limit below must count by this marker, not by `source`, or any bot can
# dodge its cap by setting an arbitrary `source` (review #1444 defect 1).
_SUBMITTER_MARKER_RE = re.compile(r"<!--\s*proposal-submitter:\s*(\S+?)\s*-->")


def _agent_limit() -> int:
    try:
        return int(os.environ.get("BOARD_PROPOSALS_AGENT_LIMIT", DEFAULT_AGENT_PROPOSAL_LIMIT))
    except ValueError:
        return DEFAULT_AGENT_PROPOSAL_LIMIT


def _extract_keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9_-]{3,}", text.lower())
    return {w for w in words if w not in _STOP_WORDS}


def _submitter_marker(submitter_id: str) -> str:
    return f"<!-- proposal-submitter: {submitter_id} -->"


def _extract_submitter(body: str) -> str | None:
    match = _SUBMITTER_MARKER_RE.search(body or "")
    return match.group(1) if match else None


def _consensus_url(meeting_date: str) -> str:
    """Link to the meeting's consensus.md on GitHub, not the dashboard's JSON API (review #1444 defect 8)."""
    return f"{RM_REPO_URL}/blob/main/docs/board-meetings/{meeting_date}/consensus.md"


def _build_issue_meeting_index(root: Path | None) -> dict[int, str]:
    """One pass over every meeting's consensus.md, mapping referenced proposal issue numbers to meeting dates.

    Replaces the old per-issue scan (``list_meetings`` + ``read_meeting`` for
    every decided proposal on every listing, O(issues * meetings)) with one
    scan over the meetings, O(meetings) (review #1444 defect 8).
    """
    index: dict[int, str] = {}
    if root is None:
        return index
    try:
        meetings = list_meetings(root)
    except OSError as exc:
        log.debug("Could not list board meetings: %s", exc)
        return index
    for meeting in meetings:
        if not meeting.get("has_consensus"):
            continue
        try:
            detail = read_meeting(root, meeting["date"])
        except (OSError, ValueError) as exc:
            log.debug("Could not read meeting %s: %s", meeting["date"], exc)
            continue
        if not detail or not detail.get("consensus_markdown"):
            continue
        text = detail["consensus_markdown"]
        for number in re.findall(r"#(\d+)", text):
            # Meetings are newest-first, so the first assignment wins —
            # matches the previous "most recent meeting" behaviour.
            index.setdefault(int(number), meeting["date"])
    return index


def _format_proposal_item(issue: dict[str, Any], meeting_index: dict[int, str] | None = None) -> ProposalItem:
    number = int(issue.get("number", 0))
    title = issue.get("title", "")
    body = issue.get("body") or ""
    parsed = store.parse_proposal_markdown(body)

    decision, decision_labels = store.extract_decision_info(issue)
    state = "decided" if (decision or issue.get("state") == "closed") else "open"

    # GitHub issues never carry top-level `target_repos`/`source`/
    # `meeting_date` fields — those only exist if something injected them
    # into the dict. Read exclusively from the parsed body and the meeting
    # index instead (review #1444 defect 5).
    meeting_date = None
    if decision and meeting_index:
        meeting_date = meeting_index.get(number)
    consensus_url = _consensus_url(meeting_date) if meeting_date else None

    return ProposalItem(
        number=number,
        title=title,
        target_repos=parsed.get("target_repos", []),
        problem=parsed.get("problem", ""),
        evidence=parsed.get("evidence", ""),
        options_considered=parsed.get("options_considered", ""),
        lean=parsed.get("lean", ""),
        estimated_cost=parsed.get("estimated_cost", ""),
        urgency=parsed.get("urgency", ""),
        source=parsed.get("source", "human"),
        code_request_url=parsed.get("code_request_url"),
        state=state,
        decision=decision,
        decision_labels=decision_labels,
        meeting_date=meeting_date,
        consensus_url=consensus_url,
        html_url=issue.get("html_url", ""),
        created_at=issue.get("created_at", ""),
        updated_at=issue.get("updated_at", ""),
        closed_at=issue.get("closed_at"),
        comments_count=int(issue.get("comments", 0)),
    )


async def _list_open_or_all_or_503(state: str) -> list[dict[str, Any]]:
    """Fetch proposal issues from GitHub, converting any read failure into a 503.

    Never lets a GitHub read failure look like "zero proposals exist" — a
    silent empty list here would let the rate limit, duplicate check, and
    listings all pass incorrectly (review #1444 defect 3).
    """
    try:
        return await store.list_github_proposals(state=state)
    except RateLimitedError:
        raise  # handled by the dashboard's own circuit-breaker exception handler
    except Exception as exc:
        log.warning("GitHub proposals fetch failed (state=%s): %s", state, exc)
        raise HTTPException(
            status_code=503,
            detail="GitHub is temporarily unavailable; cannot read Board Proposals right now",
        ) from exc


async def create_proposal(req: CreateProposalRequest, caller: Principal) -> ProposalItem:
    """Create a new Board Proposal with rate limiting and duplicate checking."""
    # 1. Determine display source. For bots, req.source is caller-controlled
    #    display text only — the rate limit below never trusts it.
    if caller.type == "bot":
        source = req.source or caller.id
    else:
        source = req.source or "human"

    # Fetch current open proposals for checks. A read failure here must not
    # silently look like "no open proposals" (defect 3) — that would also
    # bypass both the rate limit and the duplicate check below.
    open_issues = await _list_open_or_all_or_503("open")

    # 2. Rate limit for agent principals (humans unlimited), counted by the
    #    hidden submitter marker set server-side from caller.id — never by
    #    the caller-controlled `source` field (review #1444 defect 1).
    if caller.type != "human":
        limit = _agent_limit()
        open_agent_count = sum(1 for issue in open_issues if _extract_submitter(issue.get("body") or "") == caller.id)
        if open_agent_count >= limit:
            msg = (
                f"Rate limit exceeded: agent principal '{caller.id}' has {open_agent_count} open "
                f"proposals (limit is {limit})"
            )
            raise HTTPException(status_code=429, detail=msg)

    # 3. Duplicate candidate check
    if not req.confirm_not_duplicate:
        new_keywords = _extract_keywords(req.title)
        candidates: list[dict[str, Any]] = []
        for issue in open_issues:
            exist_title = issue.get("title", "")
            exist_keywords = _extract_keywords(exist_title)
            overlap = new_keywords & exist_keywords
            # Match if 2+ keywords overlap or 1 strong keyword when title is short, or substring match
            title_match = req.title.lower() in exist_title.lower() or exist_title.lower() in req.title.lower()
            if len(overlap) >= 2 or (len(overlap) >= 1 and title_match):
                candidates.append(
                    {
                        "number": issue.get("number"),
                        "title": exist_title,
                        "url": issue.get("html_url", ""),
                    }
                )
        if candidates:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "duplicate_candidates",
                    "message": "Potential duplicate proposals found. Confirm not a duplicate to proceed.",
                    "candidates": candidates,
                    "requires_confirmation": True,
                },
            )

    # 4. Render markdown (with the hidden submitter marker) and create in GitHub.
    body = store.render_proposal_markdown(req, source) + "\n" + _submitter_marker(caller.id) + "\n"
    created_issue = await store.create_github_proposal(title=req.title, body=body)
    if not created_issue.get("body"):
        created_issue["body"] = body
    return _format_proposal_item(created_issue, _build_issue_meeting_index(rm_root()))


async def list_proposals(state: str | None = None, repo: str | None = None) -> list[ProposalItem]:
    """List proposals with optional state and repo filtering."""
    issues = await _list_open_or_all_or_503("all")
    meeting_index = _build_issue_meeting_index(rm_root())
    items = [_format_proposal_item(issue, meeting_index) for issue in issues]

    # Filter by state
    if state == "open":
        items = [p for p in items if p.state == "open"]
    elif state == "decided":
        items = [p for p in items if p.state == "decided"]

    # Filter by repo
    if repo:
        repo_lower = repo.lower()
        items = [
            p for p in items if any(repo_lower in r.lower() for r in p.target_repos) or repo_lower in p.title.lower()
        ]

    return items


async def get_proposal(number: int) -> ProposalDetail:
    """Get single proposal details and secretary comments."""
    issue = await store.get_github_proposal(number)
    if not issue or not issue.get("number"):
        raise HTTPException(status_code=404, detail=f"Proposal #{number} not found")

    meeting_index = _build_issue_meeting_index(rm_root())
    base_item = _format_proposal_item(issue, meeting_index)
    try:
        raw_comments = await store.get_github_proposal_comments(number)
    except Exception as exc:
        log.warning("GitHub comments fetch failed for proposal #%d: %s", number, exc)
        raise HTTPException(
            status_code=503,
            detail=f"GitHub is temporarily unavailable; cannot read comments for proposal #{number}",
        ) from exc

    secretary_logins = _secretary_logins()
    comments: list[ProposalComment] = []
    for c in raw_comments:
        user = c.get("user") or {}
        login = str(user.get("login", "")).lower()
        is_secretary = login in secretary_logins  # pragma: allowlist secret
        comments.append(
            ProposalComment(
                id=int(c.get("id", 0)),
                user=user,
                body=str(c.get("body", "")),
                created_at=str(c.get("created_at", "")),
                is_secretary=is_secretary,
            )
        )

    return ProposalDetail(
        **base_item.model_dump(),
        comments=comments,
    )


def _secretary_logins() -> frozenset[str]:
    """Configured Board-Secretary logins (review #1444 defect 9: no substring guessing).

    Read from the environment on every call (not cached at import time) so
    tests can set ``BOARD_SECRETARY_LOGINS`` per-case via ``monkeypatch``.
    """
    raw = os.environ.get("BOARD_SECRETARY_LOGINS", "board-secretary")
    return frozenset(login.strip().lower() for login in raw.split(",") if login.strip())
