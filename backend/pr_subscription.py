"""PR lifecycle and subscription guard policy (issue #1845 / RD-0).

Enforces:
1. Default dispatched sessions to pr_lifecycle: "arm_and_exit" (open ready PR, arm auto-merge, end).
2. Auto-fix / PR subscription is allowed only on ready PRs (never on draft PRs).
3. Auto-fix / PR subscription requires explicit operator opt-in.
4. Auto-fix / PR subscription is capped at N wake-ups (default 3); upon reaching or
   exceeding the cap, sessions are handed off to RD-1 (fresh CI-fix dispatch).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

DEFAULT_PR_LIFECYCLE: str = "arm_and_exit"
DEFAULT_MAX_WAKEUPS: int = 3


class PRSubscriptionDecision(BaseModel):
    """Structured decision for PR subscription / Auto-fix requests."""

    allowed: bool = Field(..., description="Whether the PR subscription/auto-fix is admitted")
    reason: str = Field(..., description="Operator-readable explanation or failure code")
    handoff_to_rd1: bool = Field(default=False, description="True when wake-up cap reached, routing to RD-1")
    pr_lifecycle: str = Field(default=DEFAULT_PR_LIFECYCLE, description="Target lifecycle state")
    wakeups_count: int = Field(default=0, ge=0, description="Current wake-up count")
    max_wakeups: int = Field(default=DEFAULT_MAX_WAKEUPS, ge=1, description="Configured maximum wake-up cap")


def evaluate_pr_subscription(
    *,
    is_draft: bool,
    operator_opt_in: bool,
    wakeups_count: int = 0,
    max_wakeups: int = DEFAULT_MAX_WAKEUPS,
) -> PRSubscriptionDecision:
    """Evaluate whether a PR subscription or Auto-fix loop may proceed.

    Preconditions:
    - ``wakeups_count >= 0``
    - ``max_wakeups >= 1``

    Postconditions:
    - Draft PRs are NEVER allowed on Auto-fix (fail-closed).
    - Unapproved subscriptions without explicit operator opt-in default to arm_and_exit.
    - Sessions exceeding the wake-up cap are handed off to RD-1 fresh CI-fix dispatch.
    """
    if wakeups_count < 0:
        raise ValueError("wakeups_count must be non-negative")
    if max_wakeups < 1:
        raise ValueError("max_wakeups must be at least 1")

    # 1. Draft PRs are strictly forbidden from Auto-fix / subscription loops
    if is_draft:
        return PRSubscriptionDecision(
            allowed=False,
            reason="draft_pr_forbidden: Auto-fix and PR subscriptions are not allowed on draft PRs (mark ready first)",
            handoff_to_rd1=False,
            pr_lifecycle=DEFAULT_PR_LIFECYCLE,
            wakeups_count=wakeups_count,
            max_wakeups=max_wakeups,
        )

    # 2. Operator opt-in is required for long PR subscription sessions
    if not operator_opt_in:
        return PRSubscriptionDecision(
            allowed=False,
            reason=(
                "operator_opt_in_required: PR subscription and Auto-fix require explicit "
                "operator opt-in; defaulting to arm_and_exit"
            ),
            handoff_to_rd1=False,
            pr_lifecycle=DEFAULT_PR_LIFECYCLE,
            wakeups_count=wakeups_count,
            max_wakeups=max_wakeups,
        )

    # 3. Wake-up cap enforcement (default 3) -> hand off to RD-1 fresh dispatch
    if wakeups_count >= max_wakeups:
        return PRSubscriptionDecision(
            allowed=False,
            reason=(
                f"wakeup_cap_exceeded: PR session reached maximum wake-up cap ({max_wakeups}); "
                "handing off to RD-1 (fresh CI-fix dispatch) instead of continuing long session"
            ),
            handoff_to_rd1=True,
            pr_lifecycle=DEFAULT_PR_LIFECYCLE,
            wakeups_count=wakeups_count,
            max_wakeups=max_wakeups,
        )

    # 4. Admitted ready PR subscription
    return PRSubscriptionDecision(
        allowed=True,
        reason="admitted: ready PR subscribed with explicit operator opt-in within wake-up budget",
        handoff_to_rd1=False,
        pr_lifecycle="subscribed",
        wakeups_count=wakeups_count,
        max_wakeups=max_wakeups,
    )
