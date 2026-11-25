"""Drain-aware merge shepherd eligibility evaluator (#3932 slice 1)."""

from __future__ import annotations

import enum
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

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
    RETRY_SUPPRESSED = "retry_suppressed"
    ALREADY_QUEUED = "already_queued"
    ALREADY_ARMED = "already_armed"
    CHECKS_FAILED = "checks_failed"
    CHECKS_PENDING = "checks_pending"
    NO_EXACT_HEAD_PASS = "no_exact_head_pass"


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
    retry_suppressed: bool = False
    retry_suppressed_detail: str = ""


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


def has_deterministic_failure_or_unauthorized_requeue(
    timeline_events: Sequence[Mapping[str, Any]],
    comments: Sequence[Mapping[str, Any]],
    *,
    head_sha: str,
    head_committed_at: str = "",
) -> tuple[bool, str]:
    """Detect repeated deterministic failure, manual dequeue, or unarmed owner notes on unchanged head (#4658).

    Suppresses auto-requeue / re-arming unless a newer head arrived or explicit retry authorization was posted.
    """
    head_prefix = head_sha[:7].lower() if head_sha else ""

    # Check for explicit retry authorization posted after the last manual dequeue or failure
    last_dequeue_or_fail_at = ""

    # Check timeline events for dequeue / ejections since head
    for event in timeline_events:
        if not isinstance(event, Mapping):
            continue
        created_at = str(event.get("createdAt") or "")
        if head_committed_at and created_at and created_at <= head_committed_at:
            continue
        reason = str(event.get("reason") or "").casefold()
        if reason == "dequeued" or (reason and reason not in NOT_A_BOUNCE):
            if not last_dequeue_or_fail_at or created_at > last_dequeue_or_fail_at:
                last_dequeue_or_fail_at = created_at

    # Check comments for dequeue, unarmed notices, or deterministic failure indicators
    manual_dequeue = False
    unarmed_direction = False
    deterministic_fail = False

    auth_after_incident = False

    for c in comments:
        if not isinstance(c, Mapping):
            continue
        body = str(c.get("body") or "")
        created_at = str(c.get("createdAt") or "")
        if head_committed_at and created_at and created_at <= head_committed_at:
            continue

        body_lower = body.lower()

        # Check for owner/supervisor retry authorization
        if (
            "authorized retry" in body_lower
            or "re-queue authorized" in body_lower
            or "requeue authorized" in body_lower
            or "transient failure verified" in body_lower
        ):
            if not last_dequeue_or_fail_at or created_at >= last_dequeue_or_fail_at:
                auth_after_incident = True

        # Check for manual dequeue
        if (
            "dequeued and disarmed" in body_lower
            or "dequeued, disarmed" in body_lower
            or "manual dequeue" in body_lower
        ):
            manual_dequeue = True
            if not last_dequeue_or_fail_at or created_at > last_dequeue_or_fail_at:
                last_dequeue_or_fail_at = created_at

        # Check for owner-directed unarmed notes
        if (
            "not armed; not merged" in body_lower
            or "unarmed per owner direction" in body_lower
            or "deliberately unarmed" in body_lower
        ):
            unarmed_direction = True

        # Check for deterministic failures (e.g. hash-seed, stale generated types, repeated merge_group failures)
        if (
            "deterministic failure" in body_lower
            or "determinism failure" in body_lower
            or "stale generated" in body_lower
            or ("merge_group twice" in body_lower and head_prefix and head_prefix in body_lower)
        ):
            deterministic_fail = True

    if auth_after_incident:
        return False, ""

    if unarmed_direction:
        return True, "unarmed_owner_direction"
    if deterministic_fail:
        return True, "deterministic_failure_unchanged_head"
    if manual_dequeue:
        return True, "manual_dequeue_without_authorization"

    return False, ""


def is_pr_held(labels: Sequence[str] = (), title: str = "") -> bool:
    """Check if PR is marked with a hold via label or title indicator."""
    return any(label_is_merge_shepherd_hold(lbl) for lbl in labels) or pr_title_indicates_hold(title)


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
    if candidate.retry_suppressed:
        return ShepherdDecision(
            ShepherdAction.SKIP,
            pr_num,
            repo,
            reason=IneligibleReason.RETRY_SUPPRESSED,
            detail=candidate.retry_suppressed_detail,
            priority=priority,
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
    action_order = {ShepherdAction.AUTO_ARM: 0, ShepherdAction.UPDATE_BRANCH: 1, ShepherdAction.SKIP: 2}
    decisions = [evaluate_pr_shepherd_eligibility(c) for c in candidates]
    return sorted(decisions, key=lambda d: (action_order.get(d.action, 9), 0 if d.priority else 1, d.pr_number))
