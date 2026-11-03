"""Drain-aware merge shepherd eligibility evaluator (#3932 slice 1)."""

from __future__ import annotations

import enum
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass


class _StrEnum(str, enum.Enum):  # noqa: UP042
    """Python 3.10 compatible StrEnum."""

    pass


logger = logging.getLogger(__name__)

MERGE_QUEUE_REPOS = frozenset({"runner_dashboard", "repository_management", "gasification_model", "rd", "rm", "gm"})
HOLD_LABELS = frozenset({"hold", "drain-hold", "do-not-merge", "dnm", "blocked", "needs-decision", "wip"})
_HOLD_TITLE = re.compile(r"\[hold\]|\(held\)|\(hold\)|^\s*hold\b", re.IGNORECASE)


class ShepherdAction(_StrEnum):
    AUTO_ARM = "auto_arm"
    UPDATE_BRANCH = "update_branch"
    SKIP = "skip"


class IneligibleReason(_StrEnum):
    NOT_OPEN = "not_open"
    DRAFT = "draft"
    CONFLICT = "conflict"
    HELD = "held"
    DRAINED = "drained"
    BOUNCE_LIMIT = "bounce_limit"
    ALREADY_QUEUED = "already_queued"
    ALREADY_ARMED = "already_armed"
    CHECKS_FAILED = "checks_failed"
    CHECKS_PENDING = "checks_pending"


@dataclass(frozen=True)
class PRCandidate:
    repo: str
    pr_number: int
    title: str = ""
    state: str = "OPEN"
    is_draft: bool = False
    mergeable: str = "MERGEABLE"
    is_behind: bool = False
    checks_status: str = "SUCCESS"
    labels: tuple[str, ...] = ()
    is_queued: bool = False
    auto_merge_enabled: bool = False
    bounces: int = 0
    is_drained: bool = False
    is_exempt: bool = False
    has_priority: bool = False


@dataclass(frozen=True)
class ShepherdDecision:
    action: ShepherdAction
    pr_number: int
    repo: str
    merge_flag: str | None = None
    reason: IneligibleReason | None = None
    detail: str = ""
    priority: bool = False


def _normalize_repo_name(repo: str) -> str:
    return str(repo).strip().split("/")[-1].casefold().replace("-", "_")


def get_auto_merge_flag(repo: str) -> str:
    """Return plain --auto for merge queue repos (RD/RM/GM), else --auto --squash."""
    return "--auto" if _normalize_repo_name(repo) in MERGE_QUEUE_REPOS else "--auto --squash"


def is_pr_held(labels: Sequence[str] = (), title: str = "") -> bool:
    """Check if PR is marked with a hold via label or title indicator."""
    return any(str(lbl).strip().casefold() in HOLD_LABELS for lbl in labels) or bool(
        title and _HOLD_TITLE.search(title)
    )


def is_priority_candidate(candidate: PRCandidate) -> bool:
    """Check whether candidate carries merge:priority label or flag."""
    return candidate.has_priority or any(str(lbl).strip().casefold() == "merge:priority" for lbl in candidate.labels)


def evaluate_pr_shepherd_eligibility(candidate: PRCandidate) -> ShepherdDecision:
    """Evaluate whether candidate PR is eligible for auto-arm, branch update, or skip."""
    repo, pr_num, priority = candidate.repo, candidate.pr_number, is_priority_candidate(candidate)

    if candidate.state.upper() != "OPEN":
        return ShepherdDecision(ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.NOT_OPEN, priority=priority)
    if candidate.is_draft:
        return ShepherdDecision(ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.DRAFT, priority=priority)
    if candidate.mergeable.upper() == "CONFLICTING":
        return ShepherdDecision(ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.CONFLICT, priority=priority)
    if is_pr_held(candidate.labels, candidate.title):
        return ShepherdDecision(ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.HELD, priority=priority)
    if candidate.bounces >= 2:
        return ShepherdDecision(
            ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.BOUNCE_LIMIT, priority=priority
        )
    if candidate.is_drained and not candidate.is_exempt:
        return ShepherdDecision(ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.DRAINED, priority=priority)
    if candidate.is_queued:
        return ShepherdDecision(
            ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.ALREADY_QUEUED, priority=priority
        )
    if candidate.is_behind:
        return ShepherdDecision(
            ShepherdAction.UPDATE_BRANCH, pr_num, repo, detail="Update branch via API", priority=priority
        )

    checks = candidate.checks_status.upper()
    if checks == "PENDING":
        return ShepherdDecision(
            ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.CHECKS_PENDING, priority=priority
        )
    if checks != "SUCCESS":
        return ShepherdDecision(
            ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.CHECKS_FAILED, priority=priority
        )
    if candidate.auto_merge_enabled:
        return ShepherdDecision(
            ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.ALREADY_ARMED, priority=priority
        )

    flag = get_auto_merge_flag(repo)
    return ShepherdDecision(
        ShepherdAction.AUTO_ARM, pr_num, repo, merge_flag=flag, detail=f"Arm with {flag}", priority=priority
    )


def rank_candidates_for_shepherding(candidates: Sequence[PRCandidate]) -> list[ShepherdDecision]:
    """Evaluate candidates and rank decisions, prioritizing merge:priority items."""
    action_order = {ShepherdAction.AUTO_ARM: 0, ShepherdAction.UPDATE_BRANCH: 1, ShepherdAction.SKIP: 2}
    decisions = [evaluate_pr_shepherd_eligibility(c) for c in candidates]
    return sorted(decisions, key=lambda d: (action_order.get(d.action, 9), 0 if d.priority else 1, d.pr_number))
