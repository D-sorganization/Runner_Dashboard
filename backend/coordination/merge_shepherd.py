"""Drain-aware merge shepherd eligibility evaluator (#3932 slice 1)."""

from __future__ import annotations

import enum
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from coordination.merge_shepherd_carry_forward import fix_pass_decision, fix_pass_escalation_notice
from coordination.merge_shepherd_exact_head import (
    DEFAULT_REVIEW_PASS_TTL_HOURS,
    HeadReviewPass,
    pass_at_head,
    shepherd_arm_eligibility,
)
from coordination.merge_shepherd_holds import (
    MERGE_SHEPHERD_HOLD_LABELS,
    label_is_merge_shepherd_hold,
    pr_title_indicates_hold,
)
from coordination.merge_shepherd_low_risk import DEFAULT_LOW_RISK_LINE_CAP
from coordination.merge_shepherd_requeue import suppression_detail


class _StrEnum(str, enum.Enum):  # noqa: UP042
    """Python 3.10 compatible StrEnum helper."""

    pass


logger = logging.getLogger(__name__)

MERGE_QUEUE_REPOS = frozenset({"runner_dashboard", "repository_management", "gasification_model", "rd", "rm", "gm"})
HOLD_LABELS = MERGE_SHEPHERD_HOLD_LABELS
#: Merge-queue ejections of one head after which the shepherd stops arming it (#3932).
BOUNCE_LIMIT = 2
#: Ejection reasons that say nothing about the PR itself.
NOT_A_BOUNCE = frozenset({"merged", "already_merged", "queue_cleared", "roll_back"})


class ShepherdAction(_StrEnum):
    AUTO_ARM = "auto_arm"
    DISARM = "disarm"
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
    NO_EXACT_HEAD_PASS = "no_exact_head_pass"
    DETERMINISTIC_REQUEUE = "deterministic_requeue"
    MANUAL_DEQUEUE = "manual_dequeue"
    INCOMPLETE_QUEUE_HISTORY = "incomplete_queue_history"
    OWNER_UNARMED = "owner_unarmed"
    INCOMPLETE_COMMENT_HISTORY = "incomplete_comment_history"
    INCOMPLETE_CHECK_PAGE = "incomplete_check_page"
    FIX_PASS_CAP = "fix_pass_cap"


_UNARMED_HOLD_REASONS = {
    "manual_dequeue": IneligibleReason.MANUAL_DEQUEUE,
    "incomplete_queue_history": IneligibleReason.INCOMPLETE_QUEUE_HISTORY,
    "owner_unarmed": IneligibleReason.OWNER_UNARMED,
    "incomplete_comment_history": IneligibleReason.INCOMPLETE_COMMENT_HISTORY,
    "incomplete_check_page": IneligibleReason.INCOMPLETE_CHECK_PAGE,
}


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
    head_sha: str = ""
    review_passes: tuple[HeadReviewPass, ...] = ()
    review_decision: str = ""
    file_paths: tuple[str, ...] = ()
    additions: int = 0
    deletions: int = 0
    line_cap: int = 0
    files_complete: bool = True
    review_pass_ttl_hours: int = DEFAULT_REVIEW_PASS_TTL_HOURS
    head_committed_at: str = ""
    queue_removals: tuple[tuple[str, str], ...] = ()
    merge_group_failures: tuple[tuple[str, str, str], ...] = ()
    comment_bodies: tuple[tuple[str, str, str], ...] = ()
    queue_history_truncated: bool = False
    comments_truncated: bool = False
    checks_truncated: bool = False
    failed_fix_passes: int = 0


@dataclass(frozen=True)
class ShepherdDecision:
    action: ShepherdAction
    pr_number: int
    repo: str
    merge_flag: str | None = None
    reason: IneligibleReason | None = None
    detail: str = ""
    priority: bool = False
    escalation: str = ""


def _fix_pass_capped(failed_fix_passes: int) -> bool:
    """True when the open cycle has used its one fix pass and still has no fresh PASS."""
    if failed_fix_passes < 0:
        return True
    return fix_pass_decision(failed_fix_passes=failed_fix_passes) == "escalate"


def _fix_pass_cap_decision(candidate: PRCandidate, *, priority: bool) -> ShepherdDecision:
    """Skip auto-arm and carry the Barb escalation notice for a capped cycle."""
    sha = str(candidate.head_sha or "").strip() or "unknown"
    notice = fix_pass_escalation_notice(
        repo=candidate.repo,
        pr=candidate.pr_number,
        failed_fix_passes=max(candidate.failed_fix_passes, 1),
        head_sha=sha,
    )
    return ShepherdDecision(
        ShepherdAction.SKIP,
        candidate.pr_number,
        candidate.repo,
        reason=IneligibleReason.FIX_PASS_CAP,
        detail=notice,
        priority=priority,
        escalation=notice,
    )


def _normalize_repo_name(repo: str) -> str:
    return str(repo).strip().split("/")[-1].casefold().replace("-", "_")


def get_auto_merge_flag(repo: str) -> str:
    """Return plain --auto for merge queue repos (RD/RM/GM), else --auto --squash."""
    return "--auto" if _normalize_repo_name(repo) in MERGE_QUEUE_REPOS else "--auto --squash"


def count_queue_bounces(node: Mapping[str, Any]) -> int:
    """Count merge-queue ejections of the PR's current head (#4249 B-04).

    Pre: ``node`` is a GraphQL pull-request node carrying
    ``timelineItems.nodes[{createdAt, reason}]`` (``RemovedFromMergeQueueEvent``) and
    ``commits.nodes[-1].commit.committedDate``.
    Post: ejections at or before the head commit time belong to an older head and are
    not counted, so a new push clears the count. With no head commit time every
    ejection counts (fail closed). A reason in ``NOT_A_BOUNCE`` never counts.
    """
    events = (node.get("timelineItems") or {}).get("nodes") or []
    commits = (node.get("commits") or {}).get("nodes") or []
    last = commits[-1] if commits and isinstance(commits[-1], Mapping) else {}
    head_at = str((last.get("commit") or {}).get("committedDate") or "")
    bounces = 0
    for event in events:
        if not isinstance(event, Mapping) or not event.get("createdAt"):
            continue
        if str(event.get("reason") or "").casefold() in NOT_A_BOUNCE:
            continue
        if head_at and str(event["createdAt"]) <= head_at:
            continue
        bounces += 1
    return bounces


def is_pr_held(labels: Sequence[str] = (), title: str = "") -> bool:
    """Check if PR is marked with a hold via label or title indicator."""
    return any(label_is_merge_shepherd_hold(lbl) for lbl in labels) or pr_title_indicates_hold(title)


def requeue_suppression(candidate: PRCandidate) -> str:
    """Why this head must not be requeued, or ``""`` when requeue is still allowed."""
    return suppression_detail(
        head_sha=candidate.head_sha,
        head_committed_at=candidate.head_committed_at,
        removals=candidate.queue_removals,
        failures=candidate.merge_group_failures,
        comments=candidate.comment_bodies,
        queue_history_truncated=candidate.queue_history_truncated,
        comments_truncated=candidate.comments_truncated,
        checks_truncated=candidate.checks_truncated,
    )


def is_priority_candidate(candidate: PRCandidate) -> bool:
    """Check whether candidate carries merge:priority label or flag."""
    return candidate.has_priority or any(str(lbl).strip().casefold() == "merge:priority" for lbl in candidate.labels)


def evaluate_pr_shepherd_eligibility(candidate: PRCandidate) -> ShepherdDecision:
    """Evaluate whether candidate PR is eligible for auto-arm, branch update, or skip."""
    repo, pr_num, priority = candidate.repo, candidate.pr_number, is_priority_candidate(candidate)

    if candidate.state.upper() != "OPEN":
        return ShepherdDecision(ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.NOT_OPEN, priority=priority)
    # Pure decision only: live disarm execution remains deferred under #2657/#2880.
    if candidate.auto_merge_enabled or candidate.is_queued:
        _, verdict_detail = pass_at_head(candidate.review_passes, pr=pr_num, head_sha=candidate.head_sha)
        if verdict_detail == "rejected_at_head":
            return ShepherdDecision(ShepherdAction.DISARM, pr_num, repo, detail=verdict_detail, priority=priority)
        suppression = requeue_suppression(candidate)
        if suppression:
            return ShepherdDecision(ShepherdAction.DISARM, pr_num, repo, detail=suppression, priority=priority)
    else:
        suppression = requeue_suppression(candidate)
        if suppression == "deterministic_merge_group":
            return ShepherdDecision(
                ShepherdAction.SKIP,
                pr_num,
                repo,
                reason=IneligibleReason.DETERMINISTIC_REQUEUE,
                detail=suppression,
                priority=priority,
            )
        if suppression in _UNARMED_HOLD_REASONS:
            return ShepherdDecision(
                ShepherdAction.SKIP,
                pr_num,
                repo,
                reason=_UNARMED_HOLD_REASONS[suppression],
                detail=suppression,
                priority=priority,
            )
    if candidate.is_draft:
        return ShepherdDecision(ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.DRAFT, priority=priority)
    if candidate.mergeable.upper() == "CONFLICTING":
        return ShepherdDecision(ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.CONFLICT, priority=priority)
    if is_pr_held(candidate.labels, candidate.title):
        return ShepherdDecision(ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.HELD, priority=priority)
    if candidate.bounces >= BOUNCE_LIMIT:
        return ShepherdDecision(
            ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.BOUNCE_LIMIT, priority=priority
        )
    if candidate.is_drained and not candidate.is_exempt:
        return ShepherdDecision(ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.DRAINED, priority=priority)
    if candidate.is_queued:
        return ShepherdDecision(
            ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.ALREADY_QUEUED, priority=priority
        )

    checks = candidate.checks_status.upper()
    if checks == "PENDING":
        return ShepherdDecision(
            ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.CHECKS_PENDING, priority=priority
        )
    if checks != "SUCCESS":
        if _fix_pass_capped(candidate.failed_fix_passes):
            return _fix_pass_cap_decision(candidate, priority=priority)
        return ShepherdDecision(
            ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.CHECKS_FAILED, priority=priority
        )
    if candidate.auto_merge_enabled:
        return ShepherdDecision(
            ShepherdAction.SKIP, pr_num, repo, reason=IneligibleReason.ALREADY_ARMED, priority=priority
        )

    head_sha = str(candidate.head_sha or "").strip()
    if not head_sha:
        return ShepherdDecision(
            ShepherdAction.SKIP,
            pr_num,
            repo,
            reason=IneligibleReason.NO_EXACT_HEAD_PASS,
            detail="missing_pass_at_head",
            priority=priority,
        )

    line_cap = candidate.line_cap or DEFAULT_LOW_RISK_LINE_CAP
    eligibility, pass_detail = shepherd_arm_eligibility(
        review_decision=candidate.review_decision or None,
        file_paths=candidate.file_paths,
        additions=candidate.additions,
        deletions=candidate.deletions,
        line_cap=line_cap,
        labels=candidate.labels,
        checks_state=candidate.checks_status,
        files_complete=candidate.files_complete,
        pr_number=pr_num,
        head_sha=head_sha,
        review_passes=candidate.review_passes,
        review_pass_ttl_hours=candidate.review_pass_ttl_hours,
    )
    if eligibility != "reviewed":
        if _fix_pass_capped(candidate.failed_fix_passes):
            return _fix_pass_cap_decision(candidate, priority=priority)
        return ShepherdDecision(
            ShepherdAction.SKIP,
            pr_num,
            repo,
            reason=IneligibleReason.NO_EXACT_HEAD_PASS,
            detail=pass_detail,
            priority=priority,
        )
    exact_head_detail = pass_detail

    flag = get_auto_merge_flag(repo)
    arm_detail = exact_head_detail or f"Arm with {flag}"
    return ShepherdDecision(
        ShepherdAction.AUTO_ARM, pr_num, repo, merge_flag=flag, detail=arm_detail, priority=priority
    )


def rank_candidates_for_shepherding(candidates: Sequence[PRCandidate]) -> list[ShepherdDecision]:
    """Evaluate candidates and rank decisions, prioritizing merge:priority items."""
    # DISARM is a safety action (#4708): it outranks every arm and skip decision.
    action_order = {
        ShepherdAction.DISARM: 0,
        ShepherdAction.AUTO_ARM: 1,
        ShepherdAction.UPDATE_BRANCH: 2,
        ShepherdAction.SKIP: 3,
    }
    decisions = [evaluate_pr_shepherd_eligibility(c) for c in candidates]
    return sorted(decisions, key=lambda d: (action_order.get(d.action, 9), 0 if d.priority else 1, d.pr_number))
