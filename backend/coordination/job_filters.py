"""Fleet Job Board S2: hold and gate filters (issue #2783, epic #2781).

Two independent, data-driven exclusion sources, both reused rather than
duplicated:

- Schedule holds from ``staff.holds.HoldsList``/``matches`` (#1726's
  guardrail/schedule split — guardrails never exclude). A hold applies to a
  job board repo the same way it applies to the scheduler in
  ``staff.scheduler``: role-agnostic here, so matching uses an empty role and
  relies on ``applies_to`` containing ``"*"`` or ``"repo:<repo>"``. An issue
  is excluded when it carries the existing ``hold:<id>`` label convention
  (``staff.hold_actions``, ``staff.rm_reconcile``) for one of those holds.
  A new hold (RD #2657, Sidekick packaging, C3, …) needs a ``HoldsList``
  entry and the matching label on affected issues — data, not code.
- The Assessment Board gate (``code_requests.board_gate``, CR-6): an issue
  still carrying ``code-request:board_review`` (``code_requests.store``) has
  not cleared Board review yet.

Fails closed: an unreadable holds list (``staff.holds.FAIL_CLOSED_HOLD_ID``)
means the schedule-hold set cannot be trusted, so the caller must treat the
whole job list as unavailable rather than publish it unfiltered.
"""

from __future__ import annotations

from staff.holds import FAIL_CLOSED_HOLD_ID, Hold, HoldsList, matches

BOARD_REVIEW_LABEL = "code-request:board_review"


def repo_schedule_holds(repo: str, holds_list: HoldsList | None = None) -> tuple[list[Hold], bool]:
    """Active schedule-kind holds applying to ``repo`` (role-agnostic), and whether the list was readable."""
    holds = (holds_list or HoldsList()).load()
    readable = not any(h.id == FAIL_CLOSED_HOLD_ID for h in holds)
    scoped = [h for h in holds if h.kind == "schedule" and matches(h, "", repo)]
    return scoped, readable


def gate_reason(lowered_labels: set[str], holds: list[Hold]) -> str | None:
    """``None`` when the issue is unheld and ungated; else the exclusion reason."""
    if BOARD_REVIEW_LABEL in lowered_labels:
        return f"gate: {BOARD_REVIEW_LABEL} (Assessment Board review not yet cleared)"
    for hold in holds:
        if f"hold:{hold.id}" in lowered_labels:
            return f"hold: {hold.text}"
    return None
