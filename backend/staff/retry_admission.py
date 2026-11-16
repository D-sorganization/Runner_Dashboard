"""Re-check dispatch gates before automatic staff retries and fallbacks (#4271)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from dispatch_routing import extract_declared_paths
from staff.exclusions import ExclusionsList
from staff.holds import HoldsList
from staff.launch_guard import check_launch_allowed

if TYPE_CHECKING:
    from staff.store import RunRecord

log = logging.getLogger("dashboard.staff.retry_admission")


def _issue_number(rec: RunRecord) -> int | None:
    if rec.target_kind != "issue":
        return None
    ref = str(rec.target_ref or "").strip().lstrip("#")
    return int(ref) if ref.isdigit() else None


def automatic_retry_admission(runner: Any, rec: RunRecord, *, holds: HoldsList | None = None) -> tuple[bool, str]:
    """Return (allowed, reason) before scheduling a retry or provider fallback."""
    role = runner.roles().get(rec.role)
    if role is None:
        return False, f"role {rec.role} is not available"

    holds_list = holds if holds is not None else HoldsList()
    hold = holds_list.blocking(role.name, rec.repo or "")
    if hold is not None:
        return False, f"hold: {hold.text}"

    issue = _issue_number(rec)
    paths = extract_declared_paths(rec.prompt)
    excluded = ExclusionsList().blocking(rec.repo or "", issue=issue, paths=paths)
    if excluded is not None:
        return False, f"excluded: {excluded.owner}: {excluded.reason}"

    if (rec.execution_mode or "local") == "cloud" and rec.repo and issue is not None:
        verdict = check_launch_allowed(rec.repo, issue, holds=holds_list.load())
        if not verdict.allowed:
            return False, verdict.reason

    if rec.repo and issue is not None:
        from staff import claim_guard  # noqa: PLC0415

        block = claim_guard.scheduler_blocker(
            rec.repo,
            issue,
            None,
            rec.role,
            store=runner.store,
        )
        if block:
            if block.startswith("claim_check_unavailable:"):
                detail = block.removeprefix("claim_check_unavailable:").strip()
                log.warning(
                    "automatic retry: claim check unavailable for %s#%s (%s); deferring",
                    rec.repo,
                    issue,
                    detail,
                )
                return False, block
            return False, block

    return True, "retry allowed"
