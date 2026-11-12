"""Pure kanban column projection for fleet work items (#4318, epic #4317).

Maps a snapshot of GitHub issue/PR state, checks, head reviews, auto-merge,
coordination leases, staff runs, and owner holds to a single column. No I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

ColumnId = Literal[
    "merged",
    "waiting_on_dieter",
    "armed",
    "review",
    "pr_open",
    "claimed",
    "issue_open",
    "unknown",
]


class KanbanColumn(StrEnum):
    MERGED = "merged"
    WAITING_ON_DIETER = "waiting_on_dieter"
    ARMED = "armed"
    REVIEW = "review"
    PR_OPEN = "pr_open"
    CLAIMED = "claimed"
    ISSUE_OPEN = "issue_open"
    UNKNOWN = "unknown"


_COLUMN_LABELS: dict[KanbanColumn, str] = {
    KanbanColumn.MERGED: "Merged",
    KanbanColumn.WAITING_ON_DIETER: "Waiting on Dieter",
    KanbanColumn.ARMED: "Armed",
    KanbanColumn.REVIEW: "Review",
    KanbanColumn.PR_OPEN: "PR open",
    KanbanColumn.CLAIMED: "Claimed",
    KanbanColumn.ISSUE_OPEN: "Issue open",
    KanbanColumn.UNKNOWN: "Unknown",
}

_PRECEDENCE: tuple[KanbanColumn, ...] = (
    KanbanColumn.MERGED,
    KanbanColumn.WAITING_ON_DIETER,
    KanbanColumn.ARMED,
    KanbanColumn.REVIEW,
    KanbanColumn.PR_OPEN,
    KanbanColumn.CLAIMED,
    KanbanColumn.ISSUE_OPEN,
)

_HOLD_LABEL_PREFIX = "hold:"


@dataclass(frozen=True, slots=True)
class KanbanSourceLink:
    kind: str
    label: str
    url: str
    available: bool = True


@dataclass(frozen=True, slots=True)
class KanbanIssueSnapshot:
    available: bool
    number: int
    state: str
    html_url: str
    labels: tuple[str, ...] = ()
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class KanbanPullRequestSnapshot:
    available: bool
    number: int
    state: str
    draft: bool
    head_sha: str
    html_url: str
    in_merge_queue: bool = False
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class KanbanClaimSnapshot:
    available: bool
    held: bool
    agent: str | None = None
    html_url: str | None = None
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class KanbanAutoMergeSnapshot:
    available: bool
    enabled: bool = False
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class KanbanChecksSnapshot:
    available: bool
    head_state: str = "unknown"
    html_url: str | None = None
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class KanbanHeadReview:
    commit_id: str
    state: str
    author: str
    html_url: str = ""


@dataclass(frozen=True, slots=True)
class KanbanReviewsSnapshot:
    available: bool
    head_sha: str
    reviews: tuple[KanbanHeadReview, ...] = ()
    review_requested: bool = False
    changes_requested_at_head: bool = False
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class KanbanOwnerWaitSnapshot:
    available: bool
    waiting: bool = False
    reasons: tuple[str, ...] = ()
    html_url: str | None = None
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class KanbanStaffRunSnapshot:
    available: bool
    run_id: str | None = None
    status: str | None = None
    html_url: str | None = None
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class KanbanWorkItemSnapshot:
    owner: str
    repo: str
    issue: KanbanIssueSnapshot
    pull_request: KanbanPullRequestSnapshot | None = None
    claim: KanbanClaimSnapshot | None = None
    auto_merge: KanbanAutoMergeSnapshot | None = None
    checks: KanbanChecksSnapshot | None = None
    reviews: KanbanReviewsSnapshot | None = None
    owner_wait: KanbanOwnerWaitSnapshot | None = None
    staff_run: KanbanStaffRunSnapshot | None = None


@dataclass(frozen=True, slots=True)
class KanbanCard:
    owner: str
    repo: str
    issue_number: int
    column: KanbanColumn
    column_label: str
    matched_columns: tuple[KanbanColumn, ...] = ()
    stale_reviews: tuple[KanbanHeadReview, ...] = ()
    reasons: tuple[str, ...] = ()
    sources: tuple[KanbanSourceLink, ...] = ()


@dataclass(frozen=True, slots=True)
class _ColumnMatch:
    column: KanbanColumn
    reason: str


def _label_names(labels: Sequence[str]) -> list[str]:
    return [name.strip() for name in labels if name.strip()]


def _hold_labels(labels: Sequence[str]) -> list[str]:
    return [name for name in _label_names(labels) if name.lower().startswith(_HOLD_LABEL_PREFIX)]


def _open_pr(snapshot: KanbanWorkItemSnapshot) -> KanbanPullRequestSnapshot | None:
    pr = snapshot.pull_request
    if pr is None or not pr.available:
        return None
    if pr.state.lower() != "open":
        return None
    return pr


def _canonical_head_sha(snapshot: KanbanWorkItemSnapshot) -> str | None:
    """Single head SHA for exact-head review comparison.

    When PR and review snapshots both carry a head SHA they must agree; a
    mismatch is treated as missing head evidence (fail-closed elsewhere).
    """
    pr = _open_pr(snapshot) or snapshot.pull_request
    reviews = snapshot.reviews
    pr_head = ""
    if pr is not None and pr.available:
        pr_head = pr.head_sha.strip()
    rev_head = ""
    if reviews is not None and reviews.available:
        rev_head = reviews.head_sha.strip()
    if pr_head and rev_head and pr_head != rev_head:
        return None
    if pr_head:
        return pr_head
    if rev_head:
        return rev_head
    return None


def _exact_head_reviews(snapshot: KanbanWorkItemSnapshot) -> list[KanbanHeadReview]:
    reviews = snapshot.reviews
    head = _canonical_head_sha(snapshot)
    if head is None or reviews is None or not reviews.available:
        return []
    return [r for r in reviews.reviews if r.commit_id == head]


def _stale_reviews(snapshot: KanbanWorkItemSnapshot) -> tuple[KanbanHeadReview, ...]:
    reviews = snapshot.reviews
    head = _canonical_head_sha(snapshot)
    if head is None or reviews is None or not reviews.available:
        return ()
    return tuple(r for r in reviews.reviews if r.commit_id and r.commit_id != head)


def _unknown(reasons: list[str], snapshot: KanbanWorkItemSnapshot) -> KanbanCard:
    return KanbanCard(
        owner=snapshot.owner,
        repo=snapshot.repo,
        issue_number=snapshot.issue.number,
        column=KanbanColumn.UNKNOWN,
        column_label=_COLUMN_LABELS[KanbanColumn.UNKNOWN],
        reasons=tuple(reasons),
        sources=_build_sources(snapshot),
        stale_reviews=_stale_reviews(snapshot),
    )


def _linked_open_pr_snapshot(snapshot: KanbanWorkItemSnapshot) -> bool:
    pr = snapshot.pull_request
    return pr is not None and pr.state.lower() == "open"


def _fail_closed_reasons(snapshot: KanbanWorkItemSnapshot) -> list[str]:
    reasons: list[str] = []
    issue = snapshot.issue
    if not issue.available:
        reasons.append("issue source unavailable")
    if issue.degraded:
        reasons.append("issue source degraded")

    pr = snapshot.pull_request
    open_pr = _open_pr(snapshot)
    linked_open = _linked_open_pr_snapshot(snapshot)

    if issue.state.lower() == "closed":
        if pr is None or not pr.available:
            reasons.append("issue closed without merge evidence")
        elif pr.state.lower() != "merged":
            reasons.append("issue closed without merged pull request")
        return reasons

    if linked_open and open_pr is None:
        reasons.append("pull request source missing or degraded")
    elif open_pr is None:
        claim = snapshot.claim
        if claim is None or not claim.available or claim.degraded:
            reasons.append("claim source missing or degraded for unclaimed-or-claimed resolution")
    else:
        if pr is None or not pr.available or pr.degraded:
            reasons.append("pull request source missing or degraded")
        if not open_pr.draft:
            armed_known = open_pr.in_merge_queue
            if not armed_known:
                auto = snapshot.auto_merge
                if auto is None or not auto.available or auto.degraded:
                    reasons.append("auto-merge source missing or degraded for open pull request")
            reviews = snapshot.reviews
            needs_review_evidence = bool(reviews and (reviews.review_requested or reviews.changes_requested_at_head))
            if needs_review_evidence and (reviews is None or not reviews.available or reviews.degraded):
                reasons.append("review source missing or degraded while review was requested")
            head = _canonical_head_sha(snapshot)
            if (
                reviews is not None
                and reviews.available
                and not open_pr.draft
                and _open_pr(snapshot) is not None
                and open_pr.head_sha.strip()
                and reviews.head_sha.strip()
                and head is None
            ):
                reasons.append("pull request head_sha disagrees with review head_sha")
    return reasons


def _match_merged(snapshot: KanbanWorkItemSnapshot) -> _ColumnMatch | None:
    pr = snapshot.pull_request
    if pr is not None and pr.available and pr.state.lower() == "merged":
        return _ColumnMatch(KanbanColumn.MERGED, f"pull request #{pr.number} merged")
    if snapshot.issue.state.lower() == "closed" and pr is not None and pr.available and pr.state.lower() == "merged":
        return _ColumnMatch(KanbanColumn.MERGED, "issue closed with merged pull request")
    return None


def _match_waiting(snapshot: KanbanWorkItemSnapshot) -> _ColumnMatch | None:
    holds = _hold_labels(snapshot.issue.labels)
    owner_wait = snapshot.owner_wait
    if owner_wait is not None and owner_wait.available and owner_wait.waiting:
        detail = owner_wait.reasons[0] if owner_wait.reasons else "owner action required"
        return _ColumnMatch(KanbanColumn.WAITING_ON_DIETER, detail)
    if holds:
        return _ColumnMatch(KanbanColumn.WAITING_ON_DIETER, f"issue label {holds[0]!r}")
    return None


def _match_armed(snapshot: KanbanWorkItemSnapshot) -> _ColumnMatch | None:
    pr = _open_pr(snapshot)
    if pr is None or pr.draft:
        return None
    auto = snapshot.auto_merge
    armed = False
    if auto is not None and auto.available and auto.enabled:
        armed = True
    if pr.in_merge_queue:
        armed = True
    if armed:
        return _ColumnMatch(KanbanColumn.ARMED, "auto-merge armed or merge queue")
    return None


def _match_review(snapshot: KanbanWorkItemSnapshot) -> _ColumnMatch | None:
    pr = _open_pr(snapshot)
    if pr is None or pr.draft:
        return None
    reviews = snapshot.reviews
    if reviews is None or not reviews.available:
        if reviews is not None and reviews.changes_requested_at_head:
            return _ColumnMatch(KanbanColumn.REVIEW, "changes requested at head (summary)")
        return None

    if reviews.changes_requested_at_head:
        return _ColumnMatch(KanbanColumn.REVIEW, "changes requested at head (summary)")

    at_head = _exact_head_reviews(snapshot)
    states = {r.state.upper() for r in at_head}
    if "CHANGES_REQUESTED" in states:
        return _ColumnMatch(KanbanColumn.REVIEW, "changes requested at head")

    if reviews.review_requested:
        if "APPROVED" not in states:
            return _ColumnMatch(KanbanColumn.REVIEW, "review requested at head")
        if any(r.state.upper() == "PENDING" for r in at_head):
            return _ColumnMatch(KanbanColumn.REVIEW, "pending review at head")
    return None


def _match_pr_open(snapshot: KanbanWorkItemSnapshot) -> _ColumnMatch | None:
    pr = _open_pr(snapshot)
    if pr is None:
        return None
    return _ColumnMatch(KanbanColumn.PR_OPEN, f"pull request #{pr.number} open")


def _match_claimed(snapshot: KanbanWorkItemSnapshot) -> _ColumnMatch | None:
    if _open_pr(snapshot) is not None:
        return None
    claim = snapshot.claim
    if claim is None or not claim.available or not claim.held:
        return None
    agent = claim.agent or "unknown agent"
    return _ColumnMatch(KanbanColumn.CLAIMED, f"leased to {agent}")


def _match_issue_open(snapshot: KanbanWorkItemSnapshot) -> _ColumnMatch | None:
    if snapshot.issue.state.lower() != "open":
        return None
    if _open_pr(snapshot) is not None:
        return None
    claim = snapshot.claim
    if claim is not None and claim.available and claim.held:
        return None
    return _ColumnMatch(KanbanColumn.ISSUE_OPEN, "open issue without lease or pull request")


_MATCHERS: tuple[Any, ...] = (
    _match_merged,
    _match_waiting,
    _match_armed,
    _match_review,
    _match_pr_open,
    _match_claimed,
    _match_issue_open,
)


def _build_sources(snapshot: KanbanWorkItemSnapshot) -> tuple[KanbanSourceLink, ...]:
    links: list[KanbanSourceLink] = []
    issue = snapshot.issue
    links.append(
        KanbanSourceLink(
            kind="issue",
            label=f"{snapshot.repo}#{issue.number}",
            url=issue.html_url,
            available=issue.available and not issue.degraded,
        )
    )
    pr = snapshot.pull_request
    if pr is not None:
        links.append(
            KanbanSourceLink(
                kind="pull_request",
                label=f"PR #{pr.number}",
                url=pr.html_url,
                available=pr.available and not pr.degraded,
            )
        )
    claim = snapshot.claim
    if claim is not None:
        links.append(
            KanbanSourceLink(
                kind="claim",
                label="coordination lease",
                url=claim.html_url or issue.html_url,
                available=claim.available and not claim.degraded,
            )
        )
    checks = snapshot.checks
    if checks is not None:
        links.append(
            KanbanSourceLink(
                kind="checks",
                label=f"checks ({checks.head_state})",
                url=checks.html_url or (pr.html_url if pr else issue.html_url),
                available=checks.available and not checks.degraded,
            )
        )
    reviews = snapshot.reviews
    if reviews is not None:
        review_url = next((r.html_url for r in reviews.reviews if r.html_url), "")
        links.append(
            KanbanSourceLink(
                kind="review",
                label="pull request reviews",
                url=review_url or (pr.html_url if pr else issue.html_url),
                available=reviews.available and not reviews.degraded,
            )
        )
    auto = snapshot.auto_merge
    if auto is not None:
        links.append(
            KanbanSourceLink(
                kind="auto_merge",
                label="auto-merge",
                url=pr.html_url if pr else issue.html_url,
                available=auto.available and not auto.degraded,
            )
        )
    owner_wait = snapshot.owner_wait
    if owner_wait is not None:
        links.append(
            KanbanSourceLink(
                kind="hold",
                label="owner wait / holds",
                url=owner_wait.html_url or issue.html_url,
                available=owner_wait.available and not owner_wait.degraded,
            )
        )
    staff = snapshot.staff_run
    if staff is not None:
        links.append(
            KanbanSourceLink(
                kind="staff_run",
                label=f"staff run {staff.run_id or 'unlinked'}",
                url=staff.html_url or issue.html_url,
                available=staff.available and not staff.degraded,
            )
        )
    return tuple(links)


def _ordered_matches(matches: list[_ColumnMatch]) -> tuple[KanbanColumn, ...]:
    matched = {m.column for m in matches}
    return tuple(col for col in _PRECEDENCE if col in matched)


def project_kanban_column(snapshot: KanbanWorkItemSnapshot) -> KanbanCard:
    """Map one work-item snapshot to a kanban card."""
    fail_closed = _fail_closed_reasons(snapshot)
    if fail_closed:
        return _unknown(fail_closed, snapshot)

    matches: list[_ColumnMatch] = []
    for matcher in _MATCHERS:
        hit = matcher(snapshot)
        if hit is not None:
            matches.append(hit)

    if not matches:
        return _unknown(["no column predicate matched"], snapshot)

    matched_columns = _ordered_matches(matches)
    chosen = matched_columns[0]
    reason = next(m.reason for m in matches if m.column == chosen)

    return KanbanCard(
        owner=snapshot.owner,
        repo=snapshot.repo,
        issue_number=snapshot.issue.number,
        column=chosen,
        column_label=_COLUMN_LABELS[chosen],
        matched_columns=matched_columns,
        stale_reviews=_stale_reviews(snapshot),
        reasons=(reason,),
        sources=_build_sources(snapshot),
    )


def snapshot_from_mapping(payload: Mapping[str, Any]) -> KanbanWorkItemSnapshot:
    """Build a snapshot from a JSON fixture or API payload (tests and future ingestion)."""

    def _issue(raw: Mapping[str, Any]) -> KanbanIssueSnapshot:
        return KanbanIssueSnapshot(
            available=bool(raw.get("available", True)),
            number=int(raw["number"]),
            state=str(raw.get("state", "open")),
            html_url=str(raw.get("html_url", "")),
            labels=tuple(str(x) for x in raw.get("labels", ())),
            degraded=bool(raw.get("degraded", False)),
        )

    def _pr(raw: Mapping[str, Any] | None) -> KanbanPullRequestSnapshot | None:
        if raw is None:
            return None
        return KanbanPullRequestSnapshot(
            available=bool(raw.get("available", True)),
            number=int(raw["number"]),
            state=str(raw.get("state", "open")),
            draft=bool(raw.get("draft", False)),
            head_sha=str(raw.get("head_sha", "")),
            html_url=str(raw.get("html_url", "")),
            in_merge_queue=bool(raw.get("in_merge_queue", False)),
            degraded=bool(raw.get("degraded", False)),
        )

    def _claim(raw: Mapping[str, Any] | None) -> KanbanClaimSnapshot | None:
        if raw is None:
            return None
        return KanbanClaimSnapshot(
            available=bool(raw.get("available", True)),
            held=bool(raw.get("held", False)),
            agent=raw.get("agent"),
            html_url=raw.get("html_url"),
            degraded=bool(raw.get("degraded", False)),
        )

    def _auto(raw: Mapping[str, Any] | None) -> KanbanAutoMergeSnapshot | None:
        if raw is None:
            return None
        return KanbanAutoMergeSnapshot(
            available=bool(raw.get("available", True)),
            enabled=bool(raw.get("enabled", False)),
            degraded=bool(raw.get("degraded", False)),
        )

    def _checks(raw: Mapping[str, Any] | None) -> KanbanChecksSnapshot | None:
        if raw is None:
            return None
        return KanbanChecksSnapshot(
            available=bool(raw.get("available", True)),
            head_state=str(raw.get("head_state", "unknown")),
            html_url=raw.get("html_url"),
            degraded=bool(raw.get("degraded", False)),
        )

    def _reviews(raw: Mapping[str, Any] | None) -> KanbanReviewsSnapshot | None:
        if raw is None:
            return None
        revs = []
        for item in raw.get("reviews", ()):
            revs.append(
                KanbanHeadReview(
                    commit_id=str(item.get("commit_id", "")),
                    state=str(item.get("state", "")),
                    author=str(item.get("author", "")),
                    html_url=str(item.get("html_url", "")),
                )
            )
        return KanbanReviewsSnapshot(
            available=bool(raw.get("available", True)),
            head_sha=str(raw.get("head_sha", "")),
            reviews=tuple(revs),
            review_requested=bool(raw.get("review_requested", False)),
            changes_requested_at_head=bool(raw.get("changes_requested_at_head", False)),
            degraded=bool(raw.get("degraded", False)),
        )

    def _owner(raw: Mapping[str, Any] | None) -> KanbanOwnerWaitSnapshot | None:
        if raw is None:
            return None
        return KanbanOwnerWaitSnapshot(
            available=bool(raw.get("available", True)),
            waiting=bool(raw.get("waiting", False)),
            reasons=tuple(str(x) for x in raw.get("reasons", ())),
            html_url=raw.get("html_url"),
            degraded=bool(raw.get("degraded", False)),
        )

    def _staff(raw: Mapping[str, Any] | None) -> KanbanStaffRunSnapshot | None:
        if raw is None:
            return None
        return KanbanStaffRunSnapshot(
            available=bool(raw.get("available", True)),
            run_id=raw.get("run_id"),
            status=raw.get("status"),
            html_url=raw.get("html_url"),
            degraded=bool(raw.get("degraded", False)),
        )

    return KanbanWorkItemSnapshot(
        owner=str(payload.get("owner", "D-sorganization")),
        repo=str(payload["repo"]),
        issue=_issue(payload["issue"]),
        pull_request=_pr(payload.get("pull_request")),
        claim=_claim(payload.get("claim")),
        auto_merge=_auto(payload.get("auto_merge")),
        checks=_checks(payload.get("checks")),
        reviews=_reviews(payload.get("reviews")),
        owner_wait=_owner(payload.get("owner_wait")),
        staff_run=_staff(payload.get("staff_run")),
    )
