"""Recognize dispatch admission labels during the worker lease ritual (#4063)."""

from __future__ import annotations

from typing import Any

from staff import claim_guard

_HOLD_BLOCKERS = (
    "merged pr",
    "open pr",
    "closing pr",
    "do-not-automate",
    "branch pushed",
    "claimed by",
    "active lease",
)


def expected_staff_session(run_id: str) -> str:
    return f"staff-{run_id}"


def _hold_blocker_present(reason_l: str) -> bool:
    """True when ``reason_l`` asserts a blocker, not its negation (e.g. RM's "no active lease")."""
    for marker in _HOLD_BLOCKERS:
        start = 0
        while True:
            idx = reason_l.find(marker, start)
            if idx == -1:
                break
            if not reason_l[:idx].rstrip().endswith("no"):
                return True
            start = idx + len(marker)
    return False


def is_own_dispatch_claim_hold(
    run_id: str,
    check: dict[str, Any],
    *,
    agent: str,
    session: str,
) -> bool:
    """True when ``check_agent_claim`` is held only because dispatch pre-applied our label."""
    if not check.get("held"):
        return False
    if session != expected_staff_session(run_id):
        return False
    applied = claim_guard.applied_label_entry(run_id)
    if applied is None:
        return False
    _repo_full, _issue, label = applied
    check_session = str(check.get("session") or "")
    if check_session and check_session != session:
        return False
    if check_session == session:
        return True
    reason_l = str(check.get("reason") or "").lower()
    if _hold_blocker_present(reason_l):
        return False
    check_agent = str(check.get("agent") or "")
    label_agent = label.removeprefix("claim:")
    if check_agent and check_agent not in {agent, "staff", label_agent}:
        return False
    if label.lower() in reason_l:
        return True
    if run_id in reason_l:
        return True
    return not check_agent and "claim:" in reason_l
