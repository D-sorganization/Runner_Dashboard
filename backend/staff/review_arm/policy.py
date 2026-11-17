"""Pure intake and arm preconditions for #4249; no GitHub mutations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from coordination.merge_shepherd import HOLD_LABELS, PRCandidate, ShepherdAction, evaluate_pr_shepherd_eligibility
from staff.review import PROVIDER_FAMILY

HOLD_LABELS = HOLD_LABELS | frozenset(
    {
        "c3",
        "on-hold",
        "on hold",
        "sidekick packaging",
        "sidekick-packaging",
        "assessment board",
        "assessment-board",
        "do-not-automate",
    }
)
HELD_PRS = frozenset(
    {
        ("Gasification_Model", 5873),
        ("Tools_Private", 2230),
        ("Repository_Management", 2796),
        ("Repository_Management", 2797),
        ("Runner_Dashboard", 4241),
        ("UpstreamDrift", 14798),
        ("AffineDrift", 5194),
        ("Gasification_Model", 5824),
        ("Gasification_Model", 5735),
        ("Gasification_Model", 5898),
        ("Gasification_Model", 5899),
        ("UpstreamDrift", 15172),
        ("UpstreamDrift", 10333),
    }
)
DEFAULT_REPOS = (
    "Runner_Dashboard",
    "Repository_Management",
    "UpstreamDrift",
    "AffineDrift",
    "Gasification_Model",
    "Tools",
    "Tools_Private",
)


@dataclass(frozen=True)
class Candidate:
    """Verified inputs; unknown checks, author or holds must never be guessed."""

    repo: str
    pr: int
    head_sha: str
    author_provider: str = ""
    title: str = ""
    labels: tuple[str, ...] = ()
    state: str = "OPEN"
    draft: bool = False
    mergeable: str = "MERGEABLE"
    merge_state: str = "CLEAN"
    checks_green: bool = False
    checks_head: str = ""
    holds_available: bool = False
    held: bool = False
    armed: bool = False
    in_merge_queue: bool = False
    blocked: bool = False
    approved: bool = False
    queued: bool = False
    reviewed: bool = False
    prior_runs: int = 0
    providers: tuple[str, ...] = ("codex", "claude")

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", self.repo) or ".." in self.repo:
            raise ValueError("repo must be a repository name")
        if type(self.pr) is not int or self.pr < 1 or not re.fullmatch(r"[0-9a-f]{40}", self.head_sha):
            raise ValueError("a candidate needs a positive PR and full lowercase SHA")
        if self.prior_runs < 0:
            raise ValueError("prior_runs must be nonnegative")


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    provider: str = ""


def decide(c: Candidate, held_prs: frozenset[tuple[str, int]] = HELD_PRS) -> Decision:
    """Arm iff exact-head approval, confirmed checks, no hold and open/non-draft.

    The caller must re-evaluate immediately before arming and pin the mutation
    to this full SHA. All candidates pass the existing shepherd's base policy.
    """
    reason = ""
    if not c.holds_available:
        reason = "holds_unavailable"
    elif c.held or (c.repo, c.pr) in (HELD_PRS | held_prs):
        reason = "held_pr"
    elif any(label.strip().casefold() in HOLD_LABELS for label in c.labels):
        reason = "hold_label"
    elif "sidekick" in c.title.casefold() and "packag" in c.title.casefold():
        reason = "hold_label"
    elif c.armed:
        reason = "already_armed"
    elif c.in_merge_queue:
        reason = "already_queued"
    elif c.blocked:
        reason = "blocked_head"
    elif c.checks_head != c.head_sha:
        reason = "checks_not_at_head"
    elif not c.checks_green:
        reason = "required_checks_not_green"
    elif c.merge_state not in {"CLEAN", "UNSTABLE", "HAS_HOOKS"}:
        reason = "merge_state"
    if reason:
        return Decision("skip", reason)
    base = evaluate_pr_shepherd_eligibility(
        PRCandidate(
            c.repo,
            c.pr,
            title=c.title,
            state=c.state,
            is_draft=c.draft,
            mergeable=c.mergeable,
            checks_status="SUCCESS",
            labels=c.labels,
        )
    )
    if base.action != ShepherdAction.AUTO_ARM:
        return Decision("skip", str(base.reason))
    if c.approved:
        return Decision("arm", "exact_head_approval")
    if c.queued or c.reviewed:
        return Decision("skip", "already_reviewed_or_queued")
    if c.prior_runs >= 4:
        return Decision("escalate", "round_cap")
    if c.author_provider not in PROVIDER_FAMILY:
        return Decision("escalate", "unknown_author")
    author_family = PROVIDER_FAMILY[c.author_provider]
    for provider in c.providers:
        if provider in {"codex", "claude"} and PROVIDER_FAMILY[provider] != author_family:
            return Decision("review", "independent_reviewer", provider)
    return Decision("escalate", "no_independent_reviewer")
