"""Multi-agent coordination for Code Request executor stage (CR-5, #1287).

Enforces fleet coordination invariants:
- Roster priority: user > maxwell-daemon > claude > codex > conductor > jules > local > gaai.
- Claim check (check_agent_claim): skip held issues, never touch issues held by
  higher-priority agents.
- Never touch issues with `do-not-automate` label (humans only).
- Post lease (post_agent_lease, TTL 2 h) and label `claim:<agent>`.
- Require PR body to contain `Fixes #N` / `Closes #N` and `agent:<agent>` label.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from code_requests.executor_models import ChildExecutionRecord

log = logging.getLogger("dashboard.code_requests.executor_coordination")

ROSTER_PRIORITY: tuple[str, ...] = (
    "user",
    "maxwell-daemon",
    "claude",
    "codex",
    "conductor",
    "jules",
    "local",
    "gaai",
)

FIXES_ISSUE_RE = re.compile(r"(?:fixes|closes|resolves)\s+#(\d+)", re.IGNORECASE)


def roster_rank(agent: str | None) -> int:
    """Return numeric roster priority rank (0=highest: user)."""
    if not agent:
        return len(ROSTER_PRIORITY)
    agent_clean = agent.strip().lower()
    for rank, member in enumerate(ROSTER_PRIORITY):
        if member in agent_clean:
            return rank
    return len(ROSTER_PRIORITY)


def is_higher_priority(holder: str, candidate: str) -> bool:
    """Return True if holder has higher roster priority than candidate."""
    return roster_rank(holder) < roster_rank(candidate)


def can_dispatch_child(
    child: ChildExecutionRecord,
    candidate_agent: str,
    *,
    labels: list[str] | None = None,
    claim_check_fn: Callable[[str, int], dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    """Check if a child issue is eligible for dispatch by candidate_agent.

    Returns:
        (can_dispatch, reason)
    """
    # 1. Never touch do-not-automate issues
    if labels and "do-not-automate" in labels:
        return False, "do-not-automate: humans only, never touch"

    # If no issue number assigned yet, coordination check is deferred to filing
    if not child.issue_number:
        return True, "no_issue_number: claim check bypassed"

    # 2. Check claim via claim_check_fn (or default claims.check)
    if claim_check_fn is None:
        try:
            from coordination.claims import check as default_check  # noqa: PLC0415

            claim_check_fn = default_check
        except ImportError:
            log.warning("coordination.claims unavailable; defaulting to fail-open check")

            def _fallback_check(_repo: str, _issue: int) -> dict[str, object]:
                return {"available": True, "held": False}

            claim_check_fn = _fallback_check

    claim_status = claim_check_fn(child.repository, child.issue_number)
    is_held = bool(claim_status.get("held"))
    holder = str(claim_status.get("agent") or "")

    if is_held:
        # Check if held by the candidate agent itself (renewal / continuation)
        if holder and holder.lower() in candidate_agent.lower():
            return True, f"continuation: held by self ({holder})"

        # Check roster priority
        if is_higher_priority(holder, candidate_agent):
            return False, f"held_by_higher_priority: issue held by higher-priority agent '{holder}'"

        # Even if not strictly higher, existing leases should be respected
        return False, f"held: issue held by agent '{holder}' with active lease"

    return True, "available"


def post_child_lease(
    child: ChildExecutionRecord,
    agent: str,
    session: str,
    ttl: int = 7200,
    *,
    lease_poster_fn: Callable[..., dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    """Post an agent lease for a child issue.

    Returns (success, receipt_or_reason).
    """
    if not child.issue_number:
        return True, "simulated_lease"

    if lease_poster_fn is None:
        try:
            from coordination.claims import claim as default_claim  # noqa: PLC0415

            lease_poster_fn = default_claim
        except ImportError:
            return True, "mock_lease_receipt"

    try:
        res = lease_poster_fn(
            child.repository,
            child.issue_number,
            agent=agent,
            session=session,
            intent=f"CR-5 executor dispatch for {child.key}",
        )
        receipt = str(res.get("lease", {}).get("receipt") or "lease_posted")
        return True, receipt
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to post lease for %s#%s: %s", child.repository, child.issue_number, exc)
        return False, f"lease_error: {exc}"


def release_child_lease(
    child: ChildExecutionRecord,
    agent: str,
    session: str,
    reason: str,
    *,
    lease_releaser_fn: Callable[..., dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    """Release an agent lease for a child issue.

    Returns (success, detail).
    """
    if not child.issue_number:
        return True, "simulated_release"

    if lease_releaser_fn is None:
        try:
            from coordination.claims import release as default_release  # noqa: PLC0415

            lease_releaser_fn = default_release
        except ImportError:
            return True, "mock_release"

    try:
        lease_releaser_fn(
            child.repository,
            child.issue_number,
            agent=agent,
            session=session,
            reason=reason,
        )
        return True, "released"
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to release lease for %s#%s: %s", child.repository, child.issue_number, exc)
        return False, f"release_error: {exc}"


def build_pr_metadata(child: ChildExecutionRecord, agent: str) -> dict[str, Any]:
    """Construct required PR body and label metadata for child issue PR."""
    issue_ref = f"Fixes #{child.issue_number}" if child.issue_number else f"Fixes {child.key}"
    handoff_text = child.handoff or child.turnover_doc
    body = f"## Summary\n\n{child.title}\n\n{issue_ref}\n\n## Turnover Handoff\n\n{handoff_text}\n"
    labels = [f"agent:{agent}"]
    return {"body": body, "labels": labels}


def validate_pr_submission(
    pr_body: str,
    pr_labels: list[str],
    issue_number: int | None,
    agent: str,
) -> tuple[bool, str]:
    """Validate that a PR body references the issue and carries the agent tag."""
    if issue_number is not None:
        matches = [int(m) for m in FIXES_ISSUE_RE.findall(pr_body)]
        if issue_number not in matches:
            return False, f"missing_reference: PR body must contain 'Fixes #{issue_number}' or 'Closes #{issue_number}'"

    expected_label = f"agent:{agent}"
    if expected_label not in pr_labels and not any(lbl.startswith("agent:") for lbl in pr_labels):
        return False, f"missing_label: PR must include '{expected_label}' label"

    return True, "valid"
